#!/usr/bin/env python3

import os, re, sys, struct
import secrets
import stat
from contextlib import contextmanager

if __package__:
    from . import huffman
else:
    import huffman

RE_MSGIDX = re.compile(r"^#([0-9a-fA-Fx]+)")
RE_MACRO = re.compile(r"^##\s*(\w+)")
RE_INCLUDE = re.compile(r'#include\s+"([^"]+)"')
RE_TEXT_CONTROL = re.compile(r"\[([^\[\]\r\n]+)\]")


class TextControlError(ValueError):
    """A message contains an unknown or malformed named control."""

class Msg:
    def __init__(self, idx, data, definiation=None, source_name=None):
        self.idx = idx
        self.data = data
        self.source_name = source_name
        if definiation is not None:
            self.definiation = definiation
        else:
            self.definiation = f"MSG_{idx:03X}"

class SourceMessage:
    def __init__(self, idx, definition, file_path, line_number, text, source_text):
        self.idx = idx
        self.definition = definition
        self.definiation = definition if definition is not None else f"MSG_{idx:03X}"
        self.file_path = file_path
        self.line_number = line_number
        self.source_name = f"{file_path}: message 0x{idx:04X}"
        self.text = text
        self.source_text = source_text

all_data = []

def GenerateFreqTable(data):
    freq_table = [0] * 0x10000

    for value in data:
        freq_table[value] += 1

    return freq_table

def load_control_chars(parse_ref):
    control_chars = {}
    with open(parse_ref, 'r', encoding='utf-8') as file:
        for line in file:
            match = re.match(r'\[(.*?)\] = (.+)', line.strip())
            if match:
                key = match.group(1)
                values = tuple(int(v.strip(), 0) for v in match.group(2).split(','))
                control_chars[key] = values
    return control_chars

def text_preprocess_line(line, offset, last_close, in_comment, protected_end):
    cleaned = []
    boundaries = []
    position = 0
    while position < len(line):
        if in_comment:
            if line.startswith("*/", position):
                boundaries.append("\0\0")
                position += 2
                in_comment = False
            else:
                boundaries.append("\0")
                position += 1
        elif position < protected_end:
            cleaned.append(line[position:protected_end])
            boundaries.append(line[position:protected_end])
            position = protected_end
        elif position == 0 and line.startswith("//"):
            boundaries.append("\0" * len(line))
            break
        elif line.startswith("/*", position) and offset + position + 2 <= last_close:
            boundaries.append("\0\0")
            position += 2
            in_comment = True
        else:
            cleaned.append(line[position])
            boundaries.append(line[position])
            position += 1
    return "".join(cleaned), "".join(boundaries), in_comment

def validate_text_controls(text, control_chars):
    position = 0
    for match in RE_TEXT_CONTROL.finditer(text):
        literal = text[position:match.start()]
        if "[" in literal or "]" in literal:
            raise TextControlError("malformed text control")
        name = match.group(1)
        if name not in control_chars:
            raise TextControlError(f"unknown text control [{name}]")
        position = match.end()
    if "[" in text[position:] or "]" in text[position:]:
        raise TextControlError("malformed text control")

def text_glyph_length(data, pos, encoding_method):
    ch = data[pos]
    if encoding_method == 'cp932':
        if (0x81 <= ch <= 0x9F or 0xE0 <= ch <= 0xFC) and pos + 1 < len(data):
            trail = data[pos + 1]
            if 0x40 <= trail <= 0xFC and trail != 0x7F:
                return 2
        return 1

    if ch == 0x81 and pos + 1 < len(data) and data[pos + 1] == 0x40:
        return 2
    if 0xC2 <= ch <= 0xDF:
        length = 2
    elif 0xE0 <= ch <= 0xEF:
        length = 3
    elif 0xF0 <= ch <= 0xF4:
        length = 4
    else:
        return 1
    try:
        data[pos:pos + length].decode('utf-8')
    except UnicodeDecodeError:
        return 1
    return length

def validate_text_bytes(data, encoding_method):
    control_starts = set()
    pos = 0
    while pos < len(data):
        ch = data[pos]
        if ch == 0:
            if pos != len(data) - 1:
                raise TextControlError("message must end with exactly one terminator")
            return control_starts
        if ch == 0x10:
            length = 3
            label = "LoadFace"
        elif ch == 0x80:
            if pos + 1 >= len(data):
                raise TextControlError("truncated extended control")
            length = 3 if data[pos + 1] <= 3 else 2
            label = "extended"
        else:
            pos += text_glyph_length(data, pos, encoding_method)
            continue
        if pos + length >= len(data):
            raise TextControlError(f"truncated {label} control")
        control_starts.add(pos)
        pos += length
    raise TextControlError("message must end with exactly one terminator")

def text_to_shiftjis_u16_array(text, control_chars):
    u16_array = []

    validate_text_controls(text, control_chars)

    pos = 0
    while pos < len(text):
        match = RE_TEXT_CONTROL.search(text, pos)
        if match:
            # Add preceding text converted to Shift-JIS
            preceding_text = text[pos:match.start()]
            if preceding_text:
                sjis_bytes = preceding_text.encode("cp932")
                u16_array.extend(struct.unpack(f'{len(sjis_bytes)//2}H', sjis_bytes))

            # Add control character value
            control_char = match.group(1)
            u16_array.extend(control_chars[control_char])

            # Move position past the control character
            pos = match.end()
        else:
            # Add remaining text converted to Shift-JIS
            remaining_text = text[pos:]
            if remaining_text:
                sjis_bytes = remaining_text.encode("cp932")
                u16_array.extend(struct.unpack(f'{len(sjis_bytes)//2}H', sjis_bytes))
            break

    byte_array = bytearray()
    for value in u16_array:
        if not 0 <= value <= 0xFFFF:
            raise TextControlError("text symbol is outside the native 16-bit range")
        byte_array.append(value & 0xFF)
        if value >> 8:
            byte_array.append(value >> 8)
    validate_text_bytes(byte_array, 'cp932')
    return u16_array

def text_to_utf8_u16_array(text, control_chars):
    validate_text_controls(text, control_chars)

    # step1: generate byte array
    byte_array = bytearray()
    pos = 0
    while pos < len(text):
        match = RE_TEXT_CONTROL.search(text, pos)
        if match:
            preceding_text = text[pos:match.start()]
            if preceding_text:
                encoded = preceding_text.encode('utf-8')
                byte_array.extend(encoded)

            control_char = match.group(1)
            for ctrl_ch in control_chars[control_char]:
                byte_array.append(ctrl_ch)

            pos = match.end()
        else:
            remaining_text = text[pos:]
            if remaining_text:
                encoded = remaining_text.encode('utf-8')
                byte_array.extend(encoded)
            break

    control_starts = validate_text_bytes(byte_array, 'utf8')

    # step2: generate u16 array
    u16_array = []
    pos = 0
    while pos < len(byte_array):
        # same as textencode::compress_string()
        _ch = byte_array[pos]

        if pos in control_starts and _ch == 0x80:
            u16_array.append(_ch)
            pos += 1
            if byte_array[pos] != 0:
                u16_array.append(byte_array[pos])
                pos += 1
        elif pos in control_starts and _ch == 0x10:
            low, high = byte_array[pos + 1:pos + 3]
            u16_array.append(_ch)
            if high != 0:
                u16_array.append(low | (high << 8))
                pos += 3
            elif low != 0:
                u16_array.append(low)
                pos += 2
            else:
                pos += 1
        elif _ch == 0:
            if pos == len(byte_array) - 1:
                u16_array.append(0)
                pos += 1
            elif byte_array[pos + 1] != 0:
                # The native decoder emits a zero only as the low byte of a pair.
                u16_array.append(byte_array[pos + 1] << 8)
                pos += 2
            else:
                raise TextControlError("native Huffman format cannot encode adjacent zero bytes")
        elif _ch == 0x23 or _ch == 0x7F or _ch == 0xE9:
            ch1 = byte_array[pos]
            pos = pos + 1

            u16_array.append(ch1)
        elif _ch >= 0x20:
            ch1 = byte_array[pos]
            if pos + 1 >= len(byte_array) or byte_array[pos + 1] == 0:
                u16_array.append(ch1)
                pos += 1
            else:
                ch2 = byte_array[pos + 1]
                pos += 2
                u16_array.append(ch1 | (ch2 << 8))
        else:
            ch1 = byte_array[pos]
            pos = pos + 1

            u16_array.append(ch1)

    return u16_array

def text_to_u16_array(text, control_chars, encoding_method, *, source_name=None):
    try:
        if encoding_method == 'cp932':
            data = text_to_shiftjis_u16_array(text, control_chars)
        else:
            data = text_to_utf8_u16_array(text, control_chars)
        if not data or data[-1] != 0 or 0 in data[:-1]:
            raise TextControlError("message must end with exactly one terminator")
        return data
    except TextControlError as error:
        if source_name is None:
            raise
        raise TextControlError(f"{source_name}: {error}") from error

def parse_source_messages(file_path, index=0, include_stack=()):
    resolved = os.path.realpath(file_path)
    if resolved in include_stack:
        chain = " -> ".join((*include_stack, resolved))
        raise ValueError(f"text include cycle: {chain}")
    include_stack = (*include_stack, resolved)

    messages = []
    current_index = index
    text = None
    definition = None
    raw_lines = None
    directive_line = None

    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()

    def finish_message():
        nonlocal current_index, text, definition, raw_lines
        messages.append(
            SourceMessage(
                current_index, definition, file_path, directive_line,
                ''.join(text), '\n'.join(raw_lines),
            )
        )
        current_index += 1
        text = None
        definition = None
        raw_lines = None

    last_close = source.rfind("*/")
    in_comment = False
    offset = 0
    for line_number, line in enumerate(source.split("\n"), 1):
        if text is not None and not in_comment and line.startswith("#"):
            finish_message()

        original = line.lstrip()
        include_match = (
            RE_INCLUDE.match(original) if text is None and not in_comment else None
        )
        protected_end = len(line) - len(original) + include_match.end() if include_match else 0
        cleaned, boundaries, in_comment = text_preprocess_line(
            line, offset, last_close, in_comment, protected_end,
        )
        offset += len(line) + 1

        if text is not None:
            text.append(cleaned)
            raw_lines.append(line)
            continue

        if include_match:
            include_file = include_match.group(1)
            include_path = os.path.join(os.path.dirname(file_path), include_file)

            if not os.path.isfile(include_path):
                raise FileNotFoundError(f"Error: File '{include_path}' does not exist.")

            messages.extend(
                parse_source_messages(include_path, current_index, include_stack)
            )
            current_index = messages[-1].idx + 1 if messages else current_index
            continue

        # Directive matching uses original columns; removed comments cannot join tokens.
        stripped = boundaries.strip()
        directive_match = RE_MSGIDX.match(stripped)
        if directive_match:
            current_index = int(directive_match.group(1), 16)
            text = []
            raw_lines = []
            directive_line = line_number
        elif stripped.startswith('##'):
            macro_match = RE_MACRO.match(stripped)
            if macro_match:
                text = []
                raw_lines = []
                directive_line = line_number
                definition = macro_match.group(1)
            else:
                raise ValueError(f"{file_path}: malformed message directive")

    if text is not None:
        if source.endswith("\n") and raw_lines and raw_lines[-1] == "":
            raw_lines.pop()
        finish_message()
    return messages

def process_file(file_path, control_chars, encoding_method, index=0):
    messages = []
    for source in parse_source_messages(file_path, index):
        data = text_to_u16_array(
            source.text, control_chars, encoding_method,
            source_name=source.source_name,
        )
        messages.append(Msg(source.idx, data, source.definition, source.source_name))
        all_data.extend(data)
    return messages

def order_messages(messages, source_name):
    if not messages:
        raise ValueError(f"{source_name}: no messages")

    by_id = {}
    by_name = {}
    for msg in messages:
        if msg.idx in by_id:
            raise ValueError(f"{msg.source_name}: duplicate message ID 0x{msg.idx:04X}")
        if msg.definiation in ("MSG_H", "MSG_COUNT"):
            raise ValueError(f"{msg.source_name}: reserved message definition {msg.definiation}")
        if msg.definiation in by_name:
            raise ValueError(f"{msg.source_name}: duplicate message definition {msg.definiation}")
        by_id[msg.idx] = msg
        by_name[msg.definiation] = msg

    ordered = []
    for expected, idx in enumerate(sorted(by_id)):
        if idx != expected:
            raise ValueError(
                f"{by_id[idx].source_name}: missing message ID 0x{expected:04X} "
                f"before 0x{idx:04X}"
            )
        ordered.append(by_id[idx])
    return ordered

def write_header(messages, header_file):
    header_file.write("#ifndef MSG_H\n#define MSG_H\n\n")
    header_file.write(f"// Auto generated by {sys.argv[0]}\n\n")
    for msg in messages:
        header_file.write(f"#define {msg.definiation} 0x{msg.idx:04X}\n")

    header_file.write(f"\n#define MSG_COUNT 0x{(len(messages)):04X}\n")
    header_file.write("\n#endif /* MSG_H */\n")

def write_all_compressed_data(messages, code_table, data_file):
    for msg in messages:
        data_file.write(f"static const u8 CompressedText_{msg.definiation}[] = " + "{")
        for data in huffman.CompressData(msg.data, code_table):
            data_file.write(f"0x{data:02X}, ")
        data_file.write("};\n")

def write_text_table(messages, data_file):
    data_file.write("const u8 * const gMsgTable[] = {")
    for i, msg in enumerate(messages):
        if i % 8 == 0:
            data_file.write("\n    ")
        else:
            data_file.write(" ")

        data_file.write(f"CompressedText_{msg.definiation},")
    data_file.write("\n};\n")

def write_huffman_table(huffman_table, data_file):
    data_file.write("const u32 gMsgHuffmanTable[] = {")
    for i, branch in enumerate(huffman_table):
        if i % 8 == 0:
            data_file.write("\n    ")
        else:
            data_file.write(" ")

        data_file.write(f"0x{branch:08X},")
    data_file.write("\n};\n\n")
    data_file.write(f"const u32 * const gMsgHuffmanTableRoot = gMsgHuffmanTable + 0x{(len(huffman_table) - 1):04X};\n")

def write_data(messages, code_table, huffman_table, data_file):
    data_file.write('#include "global.h"\n\n')
    write_all_compressed_data(messages, code_table, data_file)
    data_file.write("\n")
    write_huffman_table(huffman_table, data_file)
    data_file.write("\n")
    write_text_table(messages, data_file)

@contextmanager
def _staged_output(path, writer, *arguments):
    destination = os.path.realpath(path)
    directory = os.path.dirname(destination)
    temporary = os.path.join(directory, f".textprocess-{secrets.token_hex(16)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    stream = None
    try:
        try:
            mode = stat.S_IMODE(os.stat(destination).st_mode)
        except FileNotFoundError:
            mode = None
        if mode is not None:
            os.fchmod(descriptor, mode)
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = None
        with stream:
            writer(*arguments, stream)
        yield temporary, destination
    finally:
        try:
            if stream is not None and not stream.closed:
                stream.close()
            elif descriptor is not None:
                os.close(descriptor)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

def _same_output(first, second):
    try:
        previous = open(second, "rb")
    except FileNotFoundError:
        return False
    with previous, open(first, "rb") as staged:
        while True:
            chunk = staged.read(65536)
            if chunk != previous.read(65536):
                return False
            if not chunk:
                return True

def _publish_output(staged):
    temporary, destination = staged
    if not _same_output(temporary, destination):
        os.replace(temporary, destination)

def dump_msg(messages):
    for msg in messages:
        print(f"MSG_{msg.idx:04X}: ", end="")
        for data in msg.data:
            print(f"0x{data:04X} ", end="")
        print("")

def main(args):
    try:
        input_fpath = args[0]
        input_parse_ref = args[1]
        output_data = args[2]
        output_header = args[3]
        encoding_method = args[4]

    except IndexError:
        sys.exit(f"Usage: {sys.argv[0]} <text-main> <defs> <output_data> <output_header> <'cp932' or 'utf8'>")

    control_chars = load_control_chars(input_parse_ref)
    messages = order_messages(
        process_file(input_fpath, control_chars, encoding_method), input_fpath,
    )

    # generate huffman
    freq_table = GenerateFreqTable(all_data)

    huff_tree = huffman.BuildHuffmanTree(freq_table)
    if huff_tree.is_leaf():
        # The native decoder consumes a bit before checking its first leaf.
        huff_tree = huffman.HuffNodeCreateNode(huff_tree, huff_tree)

    huffman_table = huffman.BuildHuffmanTable()
    code_table = huffman.build_code_table(huff_tree)

    # Neither public path changes until both renderers and their closes succeed.
    with _staged_output(output_header, write_header, messages) as header, \
         _staged_output(output_data, write_data, messages, code_table, huffman_table) as data:
        _publish_output(header)
        _publish_output(data)

if __name__ == '__main__':
	main(sys.argv[1:])
