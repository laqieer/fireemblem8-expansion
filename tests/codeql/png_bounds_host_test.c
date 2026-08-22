#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "convert_png.h"

static int sFailures;

#define CHECK(condition) do { \
    if (!(condition)) { \
        printf("FAIL: %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        sFailures++; \
    } \
} while (0)

int main(void)
{
    static const char pngPath[] = "build/tests/codeql/bit_depth_round_trip.png";
    size_t pixelBufferSize = 0;
    size_t rowPointerSize = 0;
    unsigned char pixels[1] = { 0xA5 };
    struct Image source;
    struct Image converted;

    CHECK(ComputePngBufferSizes(32, 16, 16, &pixelBufferSize, &rowPointerSize));
    CHECK(pixelBufferSize == 256);
    CHECK(rowPointerSize == 16 * sizeof(void *));

    CHECK(!ComputePngBufferSizes((size_t)INT_MAX, 2, 16, &pixelBufferSize, &rowPointerSize));
    CHECK(!ComputePngBufferSizes(1, SIZE_MAX, 2, &pixelBufferSize, &rowPointerSize));
    CHECK(!ComputePngBufferSizes(1, 2, SIZE_MAX, &pixelBufferSize, &rowPointerSize));
    CHECK(!ComputePngBufferSizes(0, 1, 1, &pixelBufferSize, &rowPointerSize));
    CHECK(!ComputePngBufferSizes(1, 1, 1, NULL, &rowPointerSize));

    memset(&source, 0, sizeof(source));
    source.width = 8;
    source.height = 1;
    source.bitDepth = 1;
    source.pixels = pixels;
    WritePng((char *)pngPath, &source);

    memset(&converted, 0, sizeof(converted));
    converted.bitDepth = 4;
    ReadPng((char *)pngPath, &converted);
    CHECK(converted.width == 8);
    CHECK(converted.height == 1);
    CHECK(converted.bitDepth == 4);
    free(converted.pixels);
    remove(pngPath);

    if (sFailures == 0)
    {
        puts("png_bounds_host_test: ok");
        return 0;
    }

    printf("%d failure(s)\n", sFailures);
    return 1;
}
