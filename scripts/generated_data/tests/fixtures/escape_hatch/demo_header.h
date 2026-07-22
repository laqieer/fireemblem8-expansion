/* Fixture header for scripts/generated_data escape-hatch tests.
 * Declares a small allowlist of hand-written C symbols a schema field is
 * permitted to reference. Mirrors the shape of a real project header
 * (e.g. include/bmreliance.h) closely enough for the declaration scanner
 * to exercise both supported forms: function declarations and extern
 * object declarations.
 */
#ifndef GUARD_FIXTURE_DEMO_HEADER_H
#define GUARD_FIXTURE_DEMO_HEADER_H

void DemoCallback_OnSupportGained(struct Unit* unit);
s8 DemoCallback_CanSupport(struct Unit* unitA, struct Unit* unitB);

extern CONST_DATA struct DemoTable gDemoAllowlistedTable;

#endif // GUARD_FIXTURE_DEMO_HEADER_H
