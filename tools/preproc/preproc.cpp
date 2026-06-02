// preproc: expands INCBIN_{S8,U8,S16,U16,S32,U32}("path"[, "path2", ...])
// directives in a C source file into brace-enclosed integer initializer lists,
// reading the referenced binary files at build time. Everything else in the
// input is passed through verbatim. This mirrors the INCBIN mechanism used by
// pret decompilation projects so that opaque binary blobs can live in typed
// C source (src/data/*.c) instead of raw .incbin in assembly.

#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <string>
#include <vector>

static std::string g_inputPath;

static void fail(const std::string &msg)
{
    fprintf(stderr, "preproc: %s: %s\n", g_inputPath.c_str(), msg.c_str());
    exit(1);
}

static std::string readFileText(const char *path)
{
    FILE *fp = fopen(path, "rb");
    if (!fp)
        fail(std::string("cannot open '") + path + "'");
    std::string out;
    char buf[8192];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), fp)) > 0)
        out.append(buf, n);
    fclose(fp);
    return out;
}

static std::vector<uint8_t> readFileBytes(const std::string &path)
{
    FILE *fp = fopen(path.c_str(), "rb");
    if (!fp)
        fail(std::string("cannot open incbin file '") + path + "'");
    std::vector<uint8_t> out;
    uint8_t buf[8192];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), fp)) > 0)
        out.insert(out.end(), buf, buf + n);
    fclose(fp);
    return out;
}

static bool isIdentStart(char c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_';
}

static bool isIdentChar(char c)
{
    return isIdentStart(c) || (c >= '0' && c <= '9');
}

// Returns element width in bytes for a recognized INCBIN macro, or 0.
static int incbinWidth(const std::string &ident)
{
    if (ident == "INCBIN_S8" || ident == "INCBIN_U8")
        return 1;
    if (ident == "INCBIN_S16" || ident == "INCBIN_U16")
        return 2;
    if (ident == "INCBIN_S32" || ident == "INCBIN_U32")
        return 4;
    return 0;
}

int main(int argc, char **argv)
{
    if (argc != 2)
    {
        fprintf(stderr, "usage: %s <file.c>\n", argv[0]);
        return 1;
    }

    g_inputPath = argv[1];
    std::string in = readFileText(argv[1]);
    std::string out;
    out.reserve(in.size() * 2);

    size_t i = 0;
    size_t len = in.size();

    while (i < len)
    {
        char c = in[i];

        // Block comment.
        if (c == '/' && i + 1 < len && in[i + 1] == '*')
        {
            out += "/*";
            i += 2;
            while (i < len && !(in[i] == '*' && i + 1 < len && in[i + 1] == '/'))
                out += in[i++];
            if (i < len)
            {
                out += "*/";
                i += 2;
            }
            continue;
        }

        // Line comment.
        if (c == '/' && i + 1 < len && in[i + 1] == '/')
        {
            while (i < len && in[i] != '\n')
                out += in[i++];
            continue;
        }

        // String / char literal (copied verbatim, escapes respected).
        if (c == '"' || c == '\'')
        {
            char quote = c;
            out += in[i++];
            while (i < len && in[i] != quote)
            {
                if (in[i] == '\\' && i + 1 < len)
                    out += in[i++];
                out += in[i++];
            }
            if (i < len)
                out += in[i++];
            continue;
        }

        // Identifier: intercept INCBIN macros, otherwise copy verbatim.
        if (isIdentStart(c))
        {
            size_t start = i;
            while (i < len && isIdentChar(in[i]))
                i++;
            std::string ident = in.substr(start, i - start);
            int width = incbinWidth(ident);
            if (width == 0)
            {
                out += ident;
                continue;
            }

            // Parse the argument list: ( "path" [, "path"]* )
            size_t j = i;
            while (j < len && (in[j] == ' ' || in[j] == '\t' || in[j] == '\n' || in[j] == '\r'))
                j++;
            if (j >= len || in[j] != '(')
                fail(ident + ": expected '(' after macro");
            j++;

            std::vector<uint8_t> bytes;
            bool expectPath = true;
            while (true)
            {
                while (j < len && (in[j] == ' ' || in[j] == '\t' || in[j] == '\n' || in[j] == '\r'))
                    j++;
                if (j >= len)
                    fail(ident + ": unterminated argument list");
                if (in[j] == ')')
                {
                    j++;
                    break;
                }
                if (in[j] == ',')
                {
                    j++;
                    expectPath = true;
                    continue;
                }
                if (in[j] != '"')
                    fail(ident + ": expected string-literal path");
                if (!expectPath)
                    fail(ident + ": missing ',' between paths");
                j++;
                std::string path;
                while (j < len && in[j] != '"')
                {
                    if (in[j] == '\\' && j + 1 < len)
                    {
                        j++;
                        path += in[j++];
                    }
                    else
                    {
                        path += in[j++];
                    }
                }
                if (j >= len)
                    fail(ident + ": unterminated path string");
                j++; // closing quote
                std::vector<uint8_t> fb = readFileBytes(path);
                bytes.insert(bytes.end(), fb.begin(), fb.end());
                expectPath = false;
            }

            if (bytes.size() % (size_t)width != 0)
                fail(ident + ": file size not a multiple of element width");

            // Emit the initializer list.
            out += '{';
            char tmp[16];
            size_t count = bytes.size() / (size_t)width;
            for (size_t k = 0; k < count; k++)
            {
                if (k != 0)
                    out += ',';
                uint32_t val = 0;
                for (int b = 0; b < width; b++)
                    val |= (uint32_t)bytes[k * width + b] << (8 * b);
                if (width == 1)
                    snprintf(tmp, sizeof(tmp), "0x%02X", val & 0xFF);
                else if (width == 2)
                    snprintf(tmp, sizeof(tmp), "0x%04X", val & 0xFFFF);
                else
                    snprintf(tmp, sizeof(tmp), "0x%08X", val);
                out += tmp;
            }
            out += '}';
            i = j;
            continue;
        }

        out += c;
        i++;
    }

    fwrite(out.data(), 1, out.size(), stdout);
    return 0;
}
