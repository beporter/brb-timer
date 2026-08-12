#!/usr/bin/env python3
# Written entirely by ChatGPT.

"""
Extract OBS Studio's deobfuscated Twitch OAuth client ID.

Usage:
    ./obs_export_twitch_oauth_client_id.py /Path/to/executable/OBS

The extractor intentionally does not use:
    - otool
    - objdump
    - dumpbin
    - Mach-O/PE/ELF Python packages
    - dSYM files

The only external dependency is Capstone's `cstool`, which must be in PATH.

Strategy
--------
OBS builds TWITCH_CLIENTID as a 30-character printable C string and
TWITCH_HASH as a uint64_t.  The relevant source pattern is effectively:

    std::string client_id = TWITCH_CLIENTID;
    deobfuscate_str(&client_id[0], TWITCH_HASH);

followed by the Twitch token request to a known URL:

    https://auth.obsproject.com/v1/twitch/token

The extractor therefore:

  1. Parses the executable enough to locate executable code and its
     virtual addresses.
  2. Finds the literal Twitch token URL.
  3. Disassembles executable code with cstool and finds references to
     the URL.
  4. Examines a small code window around those references.
  5. Recovers 64-bit constants loaded by the normal x86-64 MOV-immediate
     or AArch64 MOVZ/MOVK forms.
  6. Tries those constants against every 30-byte printable string in
     the binary.
  7. Accepts only a decoded value matching [a-z0-9]{30}.

This is deliberately heuristic rather than a general-purpose decompiler.
It follows the OBS invariants that make this particular extraction stable.
If OBS changes its obfuscation implementation, compiler-generated loading
pattern, client-ID length, or Twitch token call site, this extractor should
be expected to require corresponding changes.
"""

import os
import platform
import re
import struct
import subprocess
import sys


TOKEN_URL = b"https://auth.obsproject.com/v1/twitch/token"
CLIENT_ID_RE = re.compile(rb"^[a-z0-9]{30}$")
PRINTABLE_30_RE = re.compile(rb"[\x20-\x7e]{30}\x00")

# We only need the two architectures OBS currently ships in the relevant
# desktop binaries.
CPU_X86_64 = 0x01000007
CPU_ARM64 = 0x0100000C

# Mach-O
MH_MAGIC = 0xFEEDFACE
MH_CIGAM = 0xCEFAEDFE
MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM_64 = 0xCFFAEDFE

FAT_MAGIC = 0xCAFEBABE
FAT_CIGAM = 0xBEBAFECA
FAT_MAGIC_64 = 0xCAFEBABF
FAT_CIGAM_64 = 0xBFBAFECA

LC_SEGMENT = 0x1
LC_SEGMENT_64 = 0x19

MACHO_MAGICS = {
  MH_MAGIC,
  MH_CIGAM,
  MH_MAGIC_64,
  MH_CIGAM_64,

  FAT_MAGIC,
  FAT_CIGAM,
  FAT_MAGIC_64,
  FAT_CIGAM_64,
}


# PE
PE_MAGIC = b"MZ"
PE_MACHINE_AMD64 = 0x8664
PE_MACHINE_ARM64 = 0xAA64
IMAGE_SCN_MEM_EXECUTE = 0x20000000

# ELF
ELF_MAGIC = b"\x7fELF"
ELFCLASS32 = 1
ELFCLASS64 = 2
ELFDATA2LSB = 1
ELFDATA2MSB = 2
EM_X86_64 = 62
EM_AARCH64 = 183
PT_LOAD = 1
PF_X = 1

# Search/disassembly sizing.
DISASM_CHUNK = 2048
CODE_WINDOW_BEFORE = 0x800
CODE_WINDOW_AFTER = 0x200


class Section:
    __slots__ = ("fileoff", "size", "vaddr")

    def __init__(self, fileoff, size, vaddr):
        self.fileoff = fileoff
        self.size = size
        self.vaddr = vaddr

    def contains_file_offset(self, off):
        return self.fileoff <= off < self.fileoff + self.size

    def file_to_va(self, off):
        return self.vaddr + (off - self.fileoff)


class Binary:
    __slots__ = ("data", "arch", "endian", "sections", "mappings", "format")

    def __init__(self, data, arch, endian, sections, mappings, fmt):
        self.data = data
        self.arch = arch
        self.endian = endian
        self.sections = sections      # executable regions for cstool
        self.mappings = mappings      # all file-backed VA mappings
        self.format = fmt


def die(message):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def u16(data, off, endian):
    return struct.unpack_from(endian + "H", data, off)[0]


def u32(data, off, endian):
    return struct.unpack_from(endian + "I", data, off)[0]


def u64(data, off, endian):
    return struct.unpack_from(endian + "Q", data, off)[0]


def normalize_executable(path):
    path = os.path.abspath(path)

    if os.path.isdir(path) and path.endswith(".app"):
        return os.path.join(path, "Contents", "MacOS", "OBS")

    if not os.path.isfile(path):
        die(f"not a file: {path}")

    return path


def host_mach_cpu():
    machine = platform.machine().lower()

    if machine in ("x86_64", "amd64"):
        return CPU_X86_64

    if machine in ("arm64", "aarch64"):
        return CPU_ARM64

    return None


def parse_macho_slice(data, base):
    magic = struct.unpack_from(">I", data, base)[0]

    if magic == MH_MAGIC_64:
        endian = ">"
        is64 = True
    elif magic == MH_CIGAM_64:
        endian = "<"
        is64 = True
    elif magic == MH_MAGIC:
        endian = ">"
        is64 = False
    elif magic == MH_CIGAM:
        endian = "<"
        is64 = False
    else:
        die("invalid Mach-O slice")

    if not is64:
        die("32-bit Mach-O OBS binaries are not supported")

    cputype = u32(data, base + 4, endian)
    ncmds = u32(data, base + 16, endian)

    if cputype not in (CPU_X86_64, CPU_ARM64):
        die(f"unsupported Mach-O CPU type 0x{cputype:x}")

    sections = []
    mappings = []
    cmd_off = base + 32

    for _ in range(ncmds):
        cmd = u32(data, cmd_off, endian)
        cmdsize = u32(data, cmd_off + 4, endian)

        if cmdsize < 8 or cmd_off + cmdsize > len(data):
            die("malformed Mach-O load command")

        if cmd == LC_SEGMENT_64:
            vmaddr = u64(data, cmd_off + 24, endian)
            vmsize = u64(data, cmd_off + 32, endian)
            fileoff = u64(data, cmd_off + 40, endian)
            filesize = u64(data, cmd_off + 48, endian)
            initprot = u32(data, cmd_off + 60, endian)
            nsects = u32(data, cmd_off + 64, endian)

            if filesize:
                if fileoff + filesize > len(data):
                    die("Mach-O segment extends past end of file")

                mappings.append(
                    Section(fileoff, filesize, vmaddr)
                )

            sec_off = cmd_off + 72

            for i in range(nsects):
                so = sec_off + i * 80

                if so + 80 > cmd_off + cmdsize:
                    die("malformed Mach-O section table")

                sectname = data[so:so + 16].split(b"\0", 1)[0]
                addr = u64(data, so + 32, endian)
                size = u64(data, so + 40, endian)
                offset = u32(data, so + 48, endian)

                if sectname == b"__text" and size:
                    sections.append(
                        Section(offset, size, addr)
                    )

            # Some unusual Mach-O layouts may not expose __text as a
            # section we can use. Fall back to the executable segment.
            if (initprot & 0x4) and filesize:
                has_text = any(
                    s.fileoff >= fileoff
                    and s.fileoff + s.size <= fileoff + filesize
                    for s in sections
                )

                if not has_text:
                    sections.append(
                        Section(fileoff, filesize, vmaddr)
                    )

        cmd_off += cmdsize

    if not sections:
        die("no executable Mach-O code section found")

    if not mappings:
        die("no file-backed Mach-O mappings found")

    arch = "x64" if cputype == CPU_X86_64 else "arm64"

    return Binary(
        data,
        arch,
        endian,
        sections,
        mappings,
        "Mach-O",
    )


def select_macho_fat_slice(data):
    magic_be = struct.unpack_from(">I", data, 0)[0]

    if magic_be in (FAT_MAGIC, FAT_MAGIC_64):
        endian = ">"
        fat64 = magic_be == FAT_MAGIC_64
    elif magic_be in (FAT_CIGAM, FAT_CIGAM_64):
        endian = "<"
        fat64 = magic_be == FAT_CIGAM_64
    else:
        die("invalid Mach-O fat binary")

    nfat = u32(data, 4, endian)
    wanted = host_mach_cpu()

    if wanted is None:
        # On an unusual host, prefer x86-64, otherwise arm64.
        wanted = CPU_X86_64

    entry_size = 32 if fat64 else 20
    entries_off = 8
    selected = None

    for i in range(nfat):
        off = entries_off + i * entry_size

        if fat64:
            cputype = u32(data, off, endian)
            slice_off = u64(data, off + 8, endian)
            slice_size = u64(data, off + 16, endian)
        else:
            cputype = u32(data, off, endian)
            slice_off = u32(data, off + 8, endian)
            slice_size = u32(data, off + 12, endian)

        if cputype == wanted:
            selected = (slice_off, slice_size)
            break

    if selected is None:
        die("Mach-O universal binary has no native x86-64/arm64 slice")

    slice_off, slice_size = selected

    if slice_off + slice_size > len(data):
        die("Mach-O slice extends past end of file")

    # Parsing the slice against its own zero-based byte array makes all
    # subsequent file offsets straightforward.
    return parse_macho_slice(data[slice_off:slice_off + slice_size], 0)


def parse_macho(data):
    magic = struct.unpack_from(">I", data, 0)[0]

    if magic in (
        FAT_MAGIC,
        FAT_CIGAM,
        FAT_MAGIC_64,
        FAT_CIGAM_64,
    ):
        return select_macho_fat_slice(data)

    return parse_macho_slice(data, 0)


def parse_pe(data):
    if len(data) < 0x40 or data[:2] != PE_MAGIC:
        die("invalid PE executable")

    pe_off = u32(data, 0x3c, "<")

    if pe_off + 24 > len(data) or data[pe_off:pe_off + 4] != b"PE\0\0":
        die("invalid PE header")

    coff = pe_off + 4
    machine = u16(data, coff, "<")
    nsects = u16(data, coff + 2, "<")
    opt_size = u16(data, coff + 16, "<")

    if machine == PE_MACHINE_AMD64:
        arch = "x64"
    elif machine == PE_MACHINE_ARM64:
        arch = "arm64"
    else:
        die(f"unsupported PE machine 0x{machine:x}")

    opt = coff + 20
    if opt + opt_size > len(data):
        die("malformed PE optional header")

    magic = u16(data, opt, "<")

    if magic == 0x20B:       # PE32+
        image_base = u64(data, opt + 24, "<")
    elif magic == 0x10B:     # PE32
        image_base = u32(data, opt + 28, "<")
    else:
        die("unsupported PE optional-header format")

    sec_off = opt + opt_size
    sections = []
    mappings = []

    for i in range(nsects):
        so = sec_off + i * 40
        if so + 40 > len(data):
            die("malformed PE section table")

        virtual_size = u32(data, so + 8, "<")
        virtual_address = u32(data, so + 12, "<")
        raw_size = u32(data, so + 16, "<")
        raw_offset = u32(data, so + 20, "<")
        characteristics = u32(data, so + 36, "<")

        if not (characteristics & IMAGE_SCN_MEM_EXECUTE):
            continue

        size = min(raw_size, max(virtual_size, raw_size))
        if not size:
            continue

        if raw_offset + size > len(data):
            continue

        sections.append(
            Section(
                raw_offset,
                size,
                image_base + virtual_address,
            )
        )

        if not raw_size:
            continue

        if raw_offset + raw_size > len(data):
            continue

        mapping = Section(
            raw_offset,
            raw_size,
            image_base + virtual_address,
        )

        mappings.append(mapping)

        if characteristics & IMAGE_SCN_MEM_EXECUTE:
            sections.append(mapping)

    if not sections:
        die("no executable PE section found")

    return Binary(data, arch, "<", sections, mappings, "PE")


def parse_elf(data):
    if len(data) < 64 or data[:4] != ELF_MAGIC:
        die("invalid ELF executable")

    elf_class = data[4]
    data_encoding = data[5]

    if elf_class != ELFCLASS64:
        die("32-bit ELF OBS binaries are not supported")

    if data_encoding == ELFDATA2LSB:
        endian = "<"
    elif data_encoding == ELFDATA2MSB:
        endian = ">"
    else:
        die("unsupported ELF byte order")

    e_machine = u16(data, 18, endian)

    if e_machine == EM_X86_64:
        arch = "x64"
    elif e_machine == EM_AARCH64:
        arch = "arm64"
    else:
        die(f"unsupported ELF machine {e_machine}")

    # ELF64 program-header fields:
    # e_phoff @ 32, e_phentsize @ 54, e_phnum @ 56
    phoff = u64(data, 32, endian)
    phentsize = u16(data, 54, endian)
    phnum = u16(data, 56, endian)

    sections = []
    mappings = []

    for i in range(phnum):
        po = phoff + i * phentsize

        if po + phentsize > len(data) or phentsize < 56:
            die("malformed ELF program header")

        p_type = u32(data, po, endian)
        p_flags = u32(data, po + 4, endian)

        if p_type != PT_LOAD or not (p_flags & PF_X):
            continue

        p_offset = u64(data, po + 8, endian)
        p_vaddr = u64(data, po + 16, endian)
        p_filesz = u64(data, po + 32, endian)

        if p_filesz == 0:
            continue

        if p_offset + p_filesz > len(data):
            die("ELF executable segment extends past end of file")

        sections.append(
            Section(
                p_offset,
                p_filesz,
                p_vaddr,
            )
        )

        mapping = Section(
                p_offset,
                p_filesz,
                p_vaddr,
        )
        mappings.append(mapping)

        if p_flags & PF_X:
          sections.append(mapping)

    if not sections:
        die("no executable ELF segment found")

    return Binary(data, arch, endian, sections, mappings, "ELF")


def parse_binary(data):
    if len(data) < 4:
        die("file is too small to be an executable")

    magic = data[:4]

    if magic == PE_MAGIC:
        return parse_pe(data)

    if magic == ELF_MAGIC:
        return parse_elf(data)

    if int.from_bytes(magic, byteorder="big") in MACHO_MAGICS:
        return parse_macho(data)

    die("unrecognized executable format")


def find_token_url(data):
    offsets = []
    start = 0

    while True:
        pos = data.find(TOKEN_URL, start)
        if pos < 0:
            break
        offsets.append(pos)
        start = pos + 1

    if not offsets:
        die("Twitch token endpoint string not found")

    return offsets


def file_offset_to_va(binary, off):
    for mapping in binary.mappings:
        if mapping.contains_file_offset(off):
            return mapping.file_to_va(off)
    return None


def va_to_file_offset(binary, va):
    for mapping in binary.mappings:
        if mapping.vaddr <= va < mapping.vaddr + mapping.size:
            return mapping.fileoff + (va - mapping.vaddr)
    return None


def cstool_arch(binary):
    if binary.arch == "x64":
        return "x64"
    if binary.arch == "arm64":
        return "arm64"
    die(f"no cstool mode for {binary.arch}")


def run_cstool(binary, code, address):
    if not code:
        return []

    hex_code = code.hex(" ")

    try:
        proc = subprocess.run(
            [
                "cstool",
                "-s",
                cstool_arch(binary),
                hex_code,
                f"{address:x}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        die("cstool is not in PATH")

    if proc.returncode != 0:
        die(
            "cstool failed: "
            + (proc.stderr.strip() or "unknown error")
        )

    return parse_cstool_output(proc.stdout)


def parse_cstool_output(output):
    """
    Return tuples of:

        (address, instruction_bytes, mnemonic, operand_text)

    cstool's normal output is:

        address  byte byte ...  mnemonic<TAB>operands

    The parser deliberately ignores diagnostic/detail lines.
    """
    instructions = []

    for line in output.splitlines():
        line = line.rstrip()

        m = re.match(
            r"^\s*([0-9a-fA-F]+)\s+(.+?)\s+([A-Za-z.][A-Za-z0-9_.]*)"
            r"(?:\s+(.*))?$",
            line,
        )
        if not m:
            continue

        try:
            address = int(m.group(1), 16)
        except ValueError:
            continue

        middle = m.group(2).strip()

        # Extract the contiguous sequence of byte tokens from the middle.
        tokens = middle.split()
        byte_tokens = []

        for token in tokens:
            if re.fullmatch(r"[0-9a-fA-F]{2}", token):
                byte_tokens.append(token)
            else:
                break

        if not byte_tokens:
            continue

        try:
            raw = bytes(int(x, 16) for x in byte_tokens)
        except ValueError:
            continue

        mnemonic = m.group(3).lower()
        operands = (m.group(4) or "").strip()

        instructions.append((address, raw, mnemonic, operands))

    return instructions


def disassemble_sections(binary):
    """
    Disassemble executable sections in small chunks.

    cstool consumes the byte string as a command-line argument, so keeping
    chunks small avoids command-line-size problems on Windows while also
    avoiding the instruction-count limitations of a single huge cstool call.
    """
    all_instructions = []

    for section in binary.sections:
        start = section.fileoff
        end = section.fileoff + section.size
        pos = start

        while pos < end:
            chunk_end = min(end, pos + DISASM_CHUNK)

            # Include a small overlap so a variable-length x86 instruction
            # straddling a chunk boundary can still be decoded.
            overlap_start = max(start, pos - 32)
            code = binary.data[overlap_start:chunk_end]
            address = section.file_to_va(overlap_start)

            instructions = run_cstool(binary, code, address)

            for insn in instructions:
                insn_addr, raw, mnemonic, operands = insn

                # Only retain instructions belonging to the current chunk,
                # not the overlap belonging to the previous chunk.
                if insn_addr < section.file_to_va(pos):
                    continue
                if insn_addr >= section.file_to_va(chunk_end):
                    continue

                all_instructions.append(insn)

            pos = chunk_end

    return all_instructions


def parse_x64_rip_target(address, raw, operands):
    """
    Calculate the target of a RIP-relative operand printed by Capstone.

    Example:
        lea rax, [rip + 0x1234]

    The actual target is next_instruction + displacement.
    """
    m = re.search(
        r"\[\s*rip\s*([+-])\s*(0x[0-9a-fA-F]+)\s*\]",
        operands,
        re.IGNORECASE,
    )
    if not m:
        return None

    displacement = int(m.group(2), 16)
    if m.group(1) == "-":
        displacement = -displacement

    return address + len(raw) + displacement


def parse_arm64_pc_target(address, mnemonic, operands):
    """
    Recover the PC-relative target printed by Capstone for ADR/ADRP.

    ADRP's target is page-aligned. That is enough to identify the code
    sequence referring to the token URL; the URL itself supplies the exact
    address within that page.
    """
    if mnemonic not in ("adr", "adrp"):
        return None

    m = re.search(r"#\s*(0x[0-9a-fA-F]+)", operands)
    if not m:
        return None

    return int(m.group(1), 16)


def find_url_xrefs(binary, instructions, token_vas):
    xrefs = []

    token_pages = {va & ~0xFFF for va in token_vas}

    for address, raw, mnemonic, operands in instructions:
        target = None

        if binary.arch == "x64":
            target = parse_x64_rip_target(address, raw, operands)

            if target in token_vas:
                xrefs.append(address)

        elif binary.arch == "arm64":
            target = parse_arm64_pc_target(
                address, mnemonic, operands
            )

            if target in token_vas:
                xrefs.append(address)
            elif mnemonic == "adrp" and target in token_pages:
                xrefs.append(address)

    return sorted(set(xrefs))


def x64_immediate_constants(code):
    """
    Recover common 64-bit integer loads.

    Recognized:
        mov r64, imm64
        mov r/m64, sign_extended_imm32

    The latter is the common:
        48 C7 C0+r imm32

    Returns (offset, value).
    """
    constants = []

    i = 0
    while i < len(code):
        # REX.W + B8+r + imm64
        if (
            i + 10 <= len(code)
            and 0x48 <= code[i] <= 0x4F
            and (code[i + 1] & 0xF8) == 0xB8
        ):
            value = int.from_bytes(
                code[i + 2:i + 10],
                "little",
                signed=False,
            )
            constants.append((i, value))
            i += 10
            continue

        # REX.W + C7 /0, r/m64, imm32.
        #
        # For a register destination the ModRM byte is:
        #   11 000 r/m
        if (
            i + 7 <= len(code)
            and 0x48 <= code[i] <= 0x4F
            and code[i + 1] == 0xC7
        ):
            modrm = code[i + 2]

            if (modrm & 0xC0) == 0xC0 and (modrm & 0x38) == 0:
                imm = int.from_bytes(
                    code[i + 3:i + 7],
                    "little",
                    signed=True,
                )
                constants.append((i, imm & 0xFFFFFFFFFFFFFFFF))
                i += 7
                continue

        i += 1

    return constants


def arm64_mov_wide(word):
    """
    Decode 64-bit MOVZ/MOVK.

    Returns:
        ("movz"|"movk", register, value, shift)
    or None.
    """
    # 64-bit MOVZ: D2800000
    if (word & 0xFF800000) == 0xD2800000:
        kind = "movz"
    # 64-bit MOVK: F2800000
    elif (word & 0xFF800000) == 0xF2800000:
        kind = "movk"
    else:
        return None

    rd = word & 0x1F
    hw = (word >> 21) & 0x3
    imm16 = (word >> 5) & 0xFFFF

    return kind, rd, imm16, hw * 16


def arm64_immediate_constants(code, endian):
    """
    Recover MOVZ/MOVK-built 64-bit constants.

    The normal compiler form is:

        mov  xN, #imm16
        movk xN, #imm16, lsl #16
        movk xN, #imm16, lsl #32
        movk xN, #imm16, lsl #48

    We permit the pieces to occur within a short instruction window.
    """
    byteorder = "little" if endian == "<" else "big"
    pieces = {}

    constants = []

    for off in range(0, len(code) - 3, 4):
        word = int.from_bytes(
            code[off:off + 4],
            byteorder,
            signed=False,
        )

        decoded = arm64_mov_wide(word)
        if decoded is None:
            continue

        kind, reg, imm16, shift = decoded

        if kind == "movz":
            pieces[reg] = {
                shift: imm16,
            }
        else:
            if reg not in pieces:
                continue

            pieces[reg][shift] = imm16

        # A complete 64-bit constant has all four 16-bit lanes.
        lanes = pieces.get(reg, {})
        if all(s in lanes for s in (0, 16, 32, 48)):
            value = (
                lanes[0]
                | (lanes[16] << 16)
                | (lanes[32] << 32)
                | (lanes[48] << 48)
            )
            constants.append((off, value))

    return constants


def recover_constants(binary, code, window_address):
    if binary.arch == "x64":
        return x64_immediate_constants(code)

    if binary.arch == "arm64":
        return arm64_immediate_constants(code, binary.endian)

    return []


def find_client_strings(data):
    """
    Find exactly 30 printable ASCII characters followed by NUL.

    Returning file offsets as well as strings lets the caller use location
    as a weak tie-breaker if multiple mathematically valid candidates exist.
    """
    results = []

    for m in PRINTABLE_30_RE.finditer(data):
        value = m.group()[:-1]
        results.append((m.start(), value))

    return results


def deobfuscate(value, hash_value, endian):
    """
    Python equivalent of OBS's deobfuscate_str().

    OBS walks the bytes of the uint64_t and consumes its low/high nibble
    alternately:

        byte0 low nibble
        byte0 high nibble
        byte1 low nibble
        byte1 high nibble
        ...
        byte7 high nibble
        repeat

    The operation is XOR, and the 16-nibble key repeats.
    """
    byteorder = "little" if endian == "<" else "big"
    key = hash_value.to_bytes(8, byteorder=byteorder, signed=False)

    result = bytearray(value)

    for i in range(len(result)):
        key_byte = key[(i // 2) % 8]

        if i % 2 == 0:
            nibble = key_byte & 0x0F
        else:
            nibble = (key_byte >> 4) & 0x0F

        result[i] ^= nibble

    return bytes(result)


def score_candidate(binary, xref_fileoff, hash_fileoff, string_fileoff):
    """
    Lower is better.

    The decisive criterion is still the decoded Twitch client-ID regex.
    Location only breaks ties between otherwise valid candidates.
    """
    hash_distance = abs(hash_fileoff - xref_fileoff)

    # The obfuscated string and token URL normally live in the same broad
    # read-only data region, so this is useful but intentionally weak.
    token_distance = 0
    token_offsets = [
        o for o in range(
            max(0, xref_fileoff - 0x100000),
            min(len(binary.data), xref_fileoff + 0x100000),
        )
        if False
    ]

    # Avoid scanning; just give hash proximity the overwhelming weight.
    return hash_distance * 1000 + abs(string_fileoff - xref_fileoff)


def extract(binary):
    token_offsets = find_token_url(binary.data)

    token_vas = []
    for off in token_offsets:
        va = file_offset_to_va(binary, off)
        if va is not None:
            token_vas.append(va)

    if not token_vas:
        die("Twitch token endpoint is outside the parsed image mappings")

    instructions = disassemble_sections(binary)
    xrefs = find_url_xrefs(binary, instructions, token_vas)

    if not xrefs:
        die("no code reference to the Twitch token endpoint found")

    client_strings = find_client_strings(binary.data)

    if not client_strings:
        die("no 30-character printable strings found")

    candidates = []

    for xref_va in xrefs:
        xref_fileoff = va_to_file_offset(binary, xref_va)
        if xref_fileoff is None:
            continue

        window_start = max(
            0,
            xref_fileoff - CODE_WINDOW_BEFORE,
        )
        window_end = min(
            len(binary.data),
            xref_fileoff + CODE_WINDOW_AFTER,
        )

        # Restrict the window to executable code where possible.
        section_for_xref = None
        for section in binary.sections:
            if section.contains_file_offset(xref_fileoff):
                section_for_xref = section
                break

        if section_for_xref is None:
            continue

        window_start = max(window_start, section_for_xref.fileoff)
        window_end = min(
            window_end,
            section_for_xref.fileoff + section_for_xref.size,
        )

        code = binary.data[window_start:window_end]
        constants = recover_constants(
            binary,
            code,
            section_for_xref.file_to_va(window_start),
        )

        for constant_rel, hash_value in constants:
            hash_fileoff = window_start + constant_rel

            for string_fileoff, obfuscated in client_strings:
                decoded = deobfuscate(
                    obfuscated,
                    hash_value,
                    binary.endian,
                )

                if not CLIENT_ID_RE.fullmatch(decoded):
                    continue

                score = score_candidate(
                    binary,
                    xref_fileoff,
                    hash_fileoff,
                    string_fileoff,
                )

                candidates.append(
                    (
                        score,
                        decoded.decode("ascii"),
                        hash_value,
                        obfuscated,
                        xref_fileoff,
                        hash_fileoff,
                        string_fileoff,
                    )
                )

    if not candidates:
        die(
            "found the Twitch token call site, but could not recover "
            "a valid 30-character Twitch client ID"
        )

    candidates.sort(key=lambda x: x[0])

    # A valid lowercase/digit 30-character result is already an extremely
    # strong discriminator. Only the location-based score is used to choose
    # among duplicates.
    best = candidates[0]

    print(f"TWITCH_CLIENTID={best[3].decode('ascii')}")
    print(f"TWITCH_HASH=0x{best[2]:016x}")
    print(f"TWITCH_CLIENTID_DECODED={best[1]}")


def main():
    if len(sys.argv) != 2:
        print(
            f"usage: {os.path.basename(sys.argv[0])} /path/to/OBS",
            file=sys.stderr,
        )
        raise SystemExit(2)

    path = normalize_executable(sys.argv[1])

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        die(f"cannot read {path}: {exc}")

    binary = parse_binary(data)
    extract(binary)


if __name__ == "__main__":
    main()
