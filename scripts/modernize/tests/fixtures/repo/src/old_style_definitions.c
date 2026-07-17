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
