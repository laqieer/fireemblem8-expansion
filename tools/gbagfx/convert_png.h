// Copyright (c) 2015 YamaArashi

#ifndef CONVERT_PNG_H
#define CONVERT_PNG_H

#include <stddef.h>

#include "gfx.h"

bool ComputePngBufferSizes(
    size_t width,
    size_t height,
    size_t rowbytes,
    size_t *pixelBufferSize,
    size_t *rowPointerSize);
bool IsSupportedPngBitDepth(int bitDepth);
void ReadPng(char *path, struct Image *image);
void WritePng(char *path, struct Image *image);
void ReadPngPalette(char *path, struct Palette *palette);

#endif // CONVERT_PNG_H
