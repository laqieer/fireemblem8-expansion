void SameLine() {}

int MultiLine()
{
    return 0;
}

static int
SplitParens
(
)
{
    return 0;
}

int KnrDefinition(first, second)
int first;
char * second;
{
    return first + *second;
}

#define DEFINE_OLD(name) void name() {}
DEFINE_OLD(MacroGenerated)

#if MODERN
void ConditionalDefinition()
{
}
#endif

#define OLD_RETURN_TYPE unsigned long
OLD_RETURN_TYPE MacroReturnType()
{
    return 0;
}

#define OLD_RETURN_FUNCTION() int
OLD_RETURN_FUNCTION() MacroFunctionReturn()
{
    return 0;
}

void PrototypeOnly();
void TypedVoid(void) {}
int TypedParameter(int value) { return value; }
typedef void FunctionType();
typedef void (* FunctionPointerType)(void);
void (* FunctionPointerObject)(void);
int GlobalCallInitializer = Factory();
const char * DefinitionText = "void StringDefinition() {}";
// void CommentDefinition() {}
/* void BlockCommentDefinition() {} */
int SectionObject __attribute__((section(".data"))) = 0;
int ObjectInitializer[] = { 1, 2, 3 };

void NegativeBody(void)
{
    Factory();
}

#define NOT_A_DEFINITION() Factory()
#define UNINVOKED_DEFINITION(name) void name() {}

__attribute__((noreturn)) void LeadingAttribute() {}
void __attribute__((noreturn)) MiddleAttribute() {}

__attribute__((noreturn))
void
MultilineAttribute()
{
    for (;;) {}
}

int (* ComplexReturn())(void)
{
    return 0;
}

int KnrFunctionPointer(cb)
int (* cb)();
{
    return cb();
}

#if EDGE_OLD_BRANCH
void SplitConditional()
#else
void SplitConditional(void)
#endif
{
}

/*
#define COMMENTED_DEFINITION(name) void name() {}
*/
#define COMMENTED_DEFINITION(name) int name ## _object
COMMENTED_DEFINITION(CommentedMacroInvocation);

#define REAL_BEFORE_LITERAL(name) void name() {}
REAL_BEFORE_LITERAL(RealBeforeLiteral)

const char * ContinuedDirectiveLiteral =
    "ordinary continuation \
#define LITERAL_DEFINITION(name) void name() {}";
#define LITERAL_DEFINITION(name) int literal_ ## name
LITERAL_DEFINITION(FalsePositive);

const char * EscapedBackslashDirectiveLiteral =
    "escaped backslash pair \\
#define ESCAPED_LITERAL_DEFINITION(name) void name() {}";
#define ESCAPED_LITERAL_DEFINITION(name) int escaped_literal_ ## name
ESCAPED_LITERAL_DEFINITION(EscapedFalsePositive);

int ContinuedCharacterDirectiveLiteral =
    '\
#define CHARACTER_LITERAL_DEFINITION(name) void name() {}';
#define CHARACTER_LITERAL_DEFINITION(name) int character_literal_ ## name
CHARACTER_LITERAL_DEFINITION(CharacterFalsePositive);

#define REAL_AFTER_LITERAL(name) void name() {}
REAL_AFTER_LITERAL(RealAfterLiteral)

const char * ReviewerContinuedString =
    "continued reviewer token \
/*";
#define REVIEWER_REAL_DEFINITION(name) void name() {}
REVIEWER_REAL_DEFINITION(ReviewerStringReal)

const char * SlashContinuedString =
    "continued slash token \
//";
#define SLASH_STRING_REAL_DEFINITION(name) void name() {}
SLASH_STRING_REAL_DEFINITION(SlashStringReal)

// continued line comment with fake directive \
#define LINE_COMMENT_FAKE_DEFINITION(name) void name() {}
#define LINE_COMMENT_FAKE_DEFINITION(name) int line_comment_fake_ ## name
LINE_COMMENT_FAKE_DEFINITION(LineCommentFalse);
#define LINE_COMMENT_REAL_DEFINITION(name) void name() {}
LINE_COMMENT_REAL_DEFINITION(LineCommentReal)

/*
" quote and escaped backslash \\
// line-looking comment
#define BLOCK_COMMENT_FAKE_DEFINITION(name) void name() {}
*/
#define BLOCK_COMMENT_FAKE_DEFINITION(name) int block_comment_fake_ ## name
BLOCK_COMMENT_FAKE_DEFINITION(BlockCommentFalse);
#define BLOCK_COMMENT_REAL_DEFINITION(name) void name() {}
BLOCK_COMMENT_REAL_DEFINITION(BlockCommentReal)
