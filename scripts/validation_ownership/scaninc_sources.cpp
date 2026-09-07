/* Reuse scaninc's parser and traversal with immutable source availability. */
#include <cstring>
#include "../../tools/scaninc/source_file.h"

static bool Available(const std::string &path, void *context)
{
    return static_cast<const std::set<std::string> *>(context)->count(path) != 0;
}

int main(int argc, char **argv)
{
    if (argc == 3 && std::strcmp(argv[1], "includes") == 0)
    {
        SourceFile source(argv[2]);
        for (const auto &path : source.GetIncludes())
            std::printf("%s\n", path.c_str());
        return 0;
    }
    if (argc >= 4 && std::strcmp(argv[1], "scan") == 0)
    {
        std::set<std::string> sources;
        for (int index = 3; index < argc; ++index)
            sources.insert(argv[index]);
        if (!sources.count(argv[2]))
            return 2;
        for (const auto &path : ScanIncDependencies(
                 argv[2], {"include/", ""}, Available, &sources))
            std::printf("%s\n", path.c_str());
        return 0;
    }
    return 2;
}
