/* Fixture stand-in for a hand-written event-script header that cites
   EventScr symbols the typed eventlists macros can't reach structurally
   (mirrors how src/events/ch2-eventscript.h / ch2-wm.h cite unit groups
   and world-map event scripts for the real Ch2 bundle). Referenced from
   chapterbundle test fixtures' "externalReferences" entries. */
void FixtureExternalRefUser(void)
{
    CallEvent(EventScr_EL_Turn3);
    CallEvent(EventScr_EL_Talk2);
    CallEvent(EventScr_EL_Village2);
    CallEvent(EventScr_EL_Village3);
    CallEvent(EventScrWM_EL_Decoy);
}
