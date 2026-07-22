#pragma once

/* Fixture stand-in for include/constants/chapters.h -- only the "EL" test
   chapter used by test_chapterbundle_schema.py. */
enum chapter_idx_el_fixture
{
    CHAPTER_EL = 0x00, // Fixture chapter used by chapterbundle schema tests
    CHAPTER_EL_OTHER = 0x01, // A second row, used by the mismatched-index tests
};
