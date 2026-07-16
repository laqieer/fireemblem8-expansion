#include "global.h"

extern s8 gKeyComboResetEN;
extern u32 gGameClock;

void EnableKeyComboResetEN(ProcPtr proc)
{
  gKeyComboResetEN = 1;
};

void DisableKeyComboResetEN(void)
{
  gKeyComboResetEN = 0;
};

int CheckCanKeyComboReset(void)
{
  return gKeyComboResetEN;
};

u32 GetGameClock(void) {
  return gGameClock;
};

void SetGameTime(int newTime) {
  gGameClock = newTime;
};

void IncrementGameClock(void) {
  gGameClock++;
  if (gGameClock > 215999999) gGameClock = 213840000;
};

u8 FormatTime(u32 frameTotal, u16* hours, u16* minutes, u16* seconds) {
  int framesPerMinute;
  int framesPerHour;
  
  *seconds = (frameTotal / 60) % 60;

  framesPerMinute = 3600;
  *minutes = (frameTotal / framesPerMinute) % 60;

  framesPerHour = 216000;
  *hours = frameTotal / framesPerHour;

  return (frameTotal / 30) & 1; // clock `:` on/off boolean
};