/* Minimal fixture hand-source mirroring src/events/ch2-eventinfo.h's shape,
 * scaled down to match scripts/generated_data/tests/fixtures/eventlists/valid.json.
 * Not meant to compile standalone -- only exercises the round-trip parser's
 * macro-call tokenizer/regexes (see eventlists/parser.py). */

CONST_DATA EventListScr EventListScr_EL_Turn[] = {
    TURN(0, EventScr_EL_Turn1, 1, 0, FACTION_ID_BLUE)
    TURN(0, EventScr_EL_Turn2, 2, 0, FACTION_ID_BLUE)
    END_MAIN
};

CONST_DATA EventListScr EventListScr_EL_Character[] = {
    CHAR(EVFLAG_TMP(7), EventScr_EL_Talk1, CHARACTER_EIRIKA, CHARACTER_ROSS)
    END_MAIN
};

CONST_DATA EventListScr EventListScr_EL_Location[] = {
    Village(EVFLAG_TMP(9), EventScr_EL_Village1, 4, 2)
    Armory(ShopList_EL_Armory, 5, 7)
    END_MAIN
};

CONST_DATA EventListScr EventListScr_EL_Misc[] = {
    DefeatAll(EventScr_EL_Ending)
    CauseGameOverIfLordDies
    END_MAIN
};

CONST_DATA EventListScr EventListScr_EL_SelectUnit[] = {
    END_MAIN
};

CONST_DATA EventListScr EventListScr_EL_SelectDestination[] = {
    END_MAIN
};

CONST_DATA EventListScr EventListScr_EL_UnitMove[] = {
    END_MAIN
};

CONST_DATA EventListScr * EventListScr_EL_Tutorial[] = {
    EventScr_EL_Tutorial1,
    EventScr_EL_Tutorial2,
    EventScr_EL_Tutorial3,
    EventScr_EL_Tutorial4,
    EventScr_EL_Tutorial5,
    EventScr_EL_Tutorial6,
    EventScr_EL_Tutorial7,
    EventScr_EL_Tutorial8,
    EventScr_EL_Tutorial9,
    EventScr_EL_Tutorial10,
    EventScr_EL_Tutorial11,
    EventScr_EL_Tutorial12,
    EventScr_EL_Tutorial13,
    EventScr_EL_Tutorial14,
    EventScr_EL_Tutorial15,
    EventScr_EL_Tutorial16,
    EventScr_EL_Tutorial17,
    EventScr_EL_Tutorial18,
    EventScr_EL_Tutorial19,
    EventScr_EL_Tutorial20,
    EventScr_EL_Tutorial21,
    EventScr_EL_Tutorial22,
    EventScr_EL_Tutorial23,
    EventScr_EL_Tutorial24,
    EventScr_EL_Tutorial25,
    EventScr_EL_Tutorial26,
    EventScr_EL_Tutorial27,
    EventScr_EL_Tutorial28,
    EventScr_EL_Tutorial29,
    EventScr_EL_Tutorial30,
    NULL
};

CONST_DATA struct ChapterEventGroup ELEvents = {
    .turnBasedEvents               = EventListScr_EL_Turn,
    .characterBasedEvents          = EventListScr_EL_Character,
    .locationBasedEvents           = EventListScr_EL_Location,
    .miscBasedEvents               = EventListScr_EL_Misc,
    .specialEventsWhenUnitSelected = EventListScr_EL_SelectUnit,
    .specialEventsWhenDestSelected = EventListScr_EL_SelectDestination,
    .specialEventsAfterUnitMoved   = EventListScr_EL_UnitMove,
    .tutorialEvents                = EventListScr_EL_Tutorial,

    .traps            = TrapData_EL_Normal,
    .extraTrapsInHard = TrapData_EL_Hard,

    .playerUnitsInNormal = UnitDef_EL_Ally,
    .playerUnitsInHard   = UnitDef_EL_Ally,

    .playerUnitsChoice1InEncounter = NULL,
    .playerUnitsChoice2InEncounter = NULL,
    .playerUnitsChoice3InEncounter = NULL,

    .enemyUnitsChoice1InEncounter = NULL,
    .enemyUnitsChoice2InEncounter = NULL,
    .enemyUnitsChoice3InEncounter = NULL,

    .beginningSceneEvents = EventScr_EL_Beginning,
    .endingSceneEvents    = EventScr_EL_Ending,
};
