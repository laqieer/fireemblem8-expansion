"""Bounded syntax tree for the publisher's closed Bash command language.

This is not a general Bash interpreter. Unsupported syntax is an error, not a
string to be skipped or guessed at. The command inventory is the authorization
authority; this module preserves the syntax that authority and phase policies
need, including expansion provenance and nested execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re


class ShellSyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class Part:
    kind: str
    value: object
    quoted: bool = False


@dataclass(frozen=True)
class Word:
    parts: tuple[Part, ...]
    assignment: str | None = None
    plain: bool = field(default=True, compare=False)

    @property
    def literal(self) -> str | None:
        if all(part.kind == "literal" for part in self.parts):
            return "".join(str(part.value) for part in self.parts)
        return None

    def keyword(self, value: str) -> bool:
        return self.plain and self.literal == value


@dataclass(frozen=True)
class Redirect:
    descriptor: str
    operator: str
    target: Word
    body: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Command:
    environment: tuple[Word, ...]
    argv: tuple[Word, ...]
    redirects: tuple[Redirect, ...]
    offset: int = field(default=0, compare=False)
    end: int = field(default=0, compare=False)
    conditional: bool = False


@dataclass(frozen=True)
class Chain:
    nodes: tuple[object, ...]
    operators: tuple[str, ...] = ()
    background: bool = False


@dataclass(frozen=True)
class Block:
    items: tuple[Chain, ...]


@dataclass(frozen=True)
class Function:
    name: str
    body: Block


@dataclass(frozen=True)
class If:
    condition: Block
    success: Block
    failure: Block | None = None


@dataclass(frozen=True)
class While:
    condition: Block
    body: Block


@dataclass(frozen=True)
class For:
    variable: str | None
    values: tuple[Word, ...]
    arithmetic: tuple[str, ...]
    body: Block


@dataclass(frozen=True)
class CaseArm:
    patterns: tuple[Word, ...]
    body: Block


@dataclass(frozen=True)
class Case:
    subject: Word
    arms: tuple[CaseArm, ...]


@dataclass(frozen=True)
class Token:
    kind: str
    value: object
    offset: int


_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)=")
_PARAMETER = re.compile(
    r"(?:[0-9]+|[!@?$#]|#?[A-Za-z_][A-Za-z_0-9]*(?:\[[A-Za-z_0-9 @*+#-]+\])?)"
)
_PARAMETER_SUFFIX = re.compile(r"[A-Za-z_][A-Za-z_0-9]*(?::[0-9]+:[0-9]+|##[^$`{}]+|-[^$`{}]*)")
_ARITHMETIC = re.compile(
    r"\$\{#[A-Za-z_][A-Za-z_0-9]*\[@\]\}|[A-Za-z_][A-Za-z_0-9]*|[0-9]+"
    r"|\+\+|--|\+=|-=|<=|>=|==|!=|[;+\-*/%<>=()]"
)
_OPERATORS = (
    ";;&", "<<<", "<<-", "&>>", "&&", "||", "|&", ";;", ";&",
    "<<", ">>", "<>", ">|", "<&", ">&", "&>", ";", "|", "&",
    "(", ")", "<", ">",
)
_REDIRECTS = frozenset({"<", ">", ">>", "<>", ">|", "<&", ">&", "&>", "&>>", "<<"})


def arithmetic_tokens(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    position = 0
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        match = _ARITHMETIC.match(text, position)
        if match is None:
            raise ShellSyntaxError("unsupported publisher arithmetic")
        tokens.append(match.group())
        position = match.end()
    if not tokens:
        raise ShellSyntaxError("empty publisher arithmetic")
    return tuple(tokens)


class _Lexer:
    def __init__(self, source: str):
        if (
            not isinstance(source, str)
            or len(source) > 128 * 1024
            or not source.isascii()
            or any(ord(char) < 32 and char not in "\n\t" for char in source)
        ):
            raise ShellSyntaxError("publisher shell exceeds the input contract")
        self.source = source
        self.position = 0
        self.tokens = 0
        self.depth = 0
        self.heredoc_skip: tuple[int, int] | None = None

    def _arithmetic(self, prefix: int) -> tuple[str, ...]:
        self.position += prefix
        start = self.position
        nesting = 0
        while self.position < len(self.source):
            if self.source.startswith("))", self.position) and nesting == 0:
                result = arithmetic_tokens(self.source[start:self.position])
                self.position += 2
                return result
            char = self.source[self.position]
            if char == "(":
                nesting += 1
            elif char == ")":
                nesting -= 1
                if nesting < 0:
                    break
            self.position += 1
        raise ShellSyntaxError("unterminated publisher arithmetic")

    def next(self) -> Token:
        self.tokens += 1
        if self.tokens > 16384:
            raise ShellSyntaxError("publisher shell token limit exceeded")
        source = self.source
        if self.heredoc_skip is not None and self.position == self.heredoc_skip[0]:
            self.position = self.heredoc_skip[1]
            self.heredoc_skip = None
        while self.position < len(source):
            char = source[self.position]
            if char in " \t":
                self.position += 1
            elif source.startswith("\\\n", self.position):
                self.position += 2
            elif char == "#":
                end = source.find("\n", self.position)
                self.position = len(source) if end < 0 else end
            else:
                break
        start = self.position
        if start == len(source):
            return Token("end", "", start)
        if source[start] == "\n":
            self.position += 1
            return Token("operator", "\n", start)
        if source.startswith("((", start):
            return Token("arithmetic", self._arithmetic(2), start)
        for operator in _OPERATORS:
            if source.startswith(operator, start):
                self.position += len(operator)
                if operator in {"<<<", "<<-", ";&", ";;&"}:
                    raise ShellSyntaxError("unregistered heredoc or case fallthrough")
                return Token("operator", operator, start)
        if source[start].isdigit():
            match = re.match(r"[0-9]+(?=[<>])", source[start:])
            if match is not None:
                self.position += len(match.group())
                return Token("descriptor", match.group(), start)
        return Token("word", self._word(), start)

    def heredoc(self, target: Word) -> str:
        if target.plain or target.literal is None or _NAME.fullmatch(target.literal) is None:
            raise ShellSyntaxError("publisher heredoc must have a quoted fixed delimiter")
        if self.heredoc_skip is not None:
            raise ShellSyntaxError("multiple publisher heredocs on one command")
        start = self.source.find("\n", self.position)
        if start < 0 or self.source[self.position:start].strip():
            raise ShellSyntaxError("publisher heredoc must finish its command")
        end = self.source.find("\n" + target.literal + "\n", start)
        if end < 0:
            raise ShellSyntaxError("unterminated publisher heredoc")
        self.heredoc_skip = (start + 1, end + len(target.literal) + 2)
        return self.source[start + 1:end + 1]

    def _word(self, *, regex: bool = False) -> Word:
        source = self.source
        start = self.position
        assignment = _ASSIGNMENT.match(source, start)
        parts: list[Part] = []
        plain = True
        quote: str | None = None

        def literal(value: str, *, active: bool = False) -> None:
            if regex and active and any(c in value for c in ".^$*+?[]{}\\|()"):
                kind = "regex"
            else:
                kind = "pattern" if active and any(c in value for c in "*?[~{") else "literal"
            if parts and parts[-1].kind == kind:
                parts[-1] = Part(kind, str(parts[-1].value) + value)
            else:
                parts.append(Part(kind, value))

        while self.position < len(source):
            char = source[self.position]
            if quote == "'":
                end = source.find("'", self.position)
                if end < 0:
                    raise ShellSyntaxError("unterminated single quote")
                literal(source[self.position:end])
                self.position = end + 1
                quote = None
                continue
            if char == "\\":
                plain = False
                if self.position + 1 == len(source):
                    raise ShellSyntaxError("unterminated shell escape")
                escaped = source[self.position + 1]
                if escaped == "\n":
                    self.position += 2
                    continue
                if quote == '"' and escaped not in '$`"\\':
                    literal("\\")
                    self.position += 1
                    continue
                literal(escaped)
                self.position += 2
                continue
            if quote == '"' and char == '"':
                self.position += 1
                quote = None
                continue
            if quote is None:
                if char in (" \t\n" if regex else " \t\n;&|<>()"):
                    break
                if char in "\"'":
                    plain = False
                    quote = char
                    self.position += 1
                    if self.position < len(source) and source[self.position] == char:
                        literal("")
                    continue
            if char == "`":
                raise ShellSyntaxError("backtick execution is not registered")
            if char == "$":
                plain = False
                if source.startswith("$'", self.position) or source.startswith('$"', self.position):
                    raise ShellSyntaxError("ANSI-C and locale quotes are not registered")
                if source.startswith("$((", self.position):
                    parts.append(Part("arithmetic", self._arithmetic(3), quote == '"'))
                    continue
                if source.startswith("$(", self.position):
                    self.position += 2
                    nested = _Parser(self).block({")"})
                    parts.append(Part("substitution", nested, quote == '"'))
                    continue
                if source.startswith("${", self.position):
                    end = source.find("}", self.position + 2)
                    if end < 0:
                        raise ShellSyntaxError("unterminated parameter")
                    parameter = source[self.position + 2:end]
                    if _PARAMETER.fullmatch(parameter) is not None:
                        parameter = re.sub(r"\s+", "", parameter)
                    elif _PARAMETER_SUFFIX.fullmatch(parameter) is None:
                        raise ShellSyntaxError("unregistered parameter expansion")
                    self.position = end + 1
                else:
                    match = re.match(r"[A-Za-z_][A-Za-z_0-9]*|[0-9!@?$#]", source[self.position + 1:])
                    if match is None:
                        literal("$", active=regex and quote is None)
                        self.position += 1
                        continue
                    parameter = match.group()
                    self.position += len(parameter) + 1
                parts.append(Part("parameter", parameter, quote == '"'))
                continue
            literal(char, active=quote is None)
            self.position += 1
        if quote is not None:
            raise ShellSyntaxError("unterminated quoted word")
        if not parts:
            raise ShellSyntaxError("empty publisher shell token")
        if not regex and any(part.kind == "pattern" and "{" in str(part.value) for part in parts):
            if len(parts) != 1 or parts[0].value != "{":
                raise ShellSyntaxError("brace expansion is not registered")
            parts = [Part("literal", "{")]
        if len(parts) > 1:
            parts = [part for part in parts if part != Part("literal", "")]
        if parts in ([Part("pattern", "[")], [Part("pattern", "[[")]):
            parts = [Part("literal", parts[0].value)]
        if plain and parts == [Part("literal", "="), Part("pattern", "~")]:
            parts = [Part("literal", "=~")]
        return Word(tuple(parts), assignment.group(1) if assignment else None, plain)


class _Parser:
    def __init__(self, lexer: _Lexer):
        self.lexer = lexer
        self.lookahead: Token | None = None

    def peek(self) -> Token:
        if self.lookahead is None:
            self.lookahead = self.lexer.next()
        return self.lookahead

    def pop(self) -> Token:
        token = self.peek()
        self.lookahead = None
        return token

    def at(self, value: str) -> bool:
        token = self.peek()
        return (
            token.kind == "operator" and token.value == value
            or token.kind == "word" and token.value.keyword(value)
        )

    def require(self, value: str) -> None:
        if not self.at(value):
            raise ShellSyntaxError(f"expected shell delimiter {value!r}")
        self.pop()

    def newlines(self) -> None:
        while self.at("\n"):
            self.pop()

    def word(self) -> Word:
        token = self.pop()
        if token.kind != "word":
            raise ShellSyntaxError("expected a publisher shell word")
        return token.value

    def block(self, stop: set[str], *, consume_stop: bool = True) -> Block:
        self.lexer.depth += 1
        if self.lexer.depth > 48:
            raise ShellSyntaxError("publisher shell nesting limit exceeded")
        items: list[Chain] = []
        try:
            self.newlines()
            while self.peek().kind != "end" and not any(self.at(end) for end in stop):
                nodes = [self.atom()]
                operators: list[str] = []
                while self.peek().kind == "operator" and self.peek().value in {"&&", "||", "|", "|&"}:
                    operators.append(self.pop().value)
                    self.newlines()
                    nodes.append(self.atom())
                background = self.at("&")
                if background:
                    self.pop()
                items.append(Chain(tuple(nodes), tuple(operators), background))
                if self.at(";") or self.at("\n"):
                    self.pop()
                    self.newlines()
                elif background:
                    self.newlines()
                elif self.peek().kind != "end" and not any(self.at(end) for end in stop):
                    raise ShellSyntaxError("missing publisher command separator")
            if stop:
                if not any(self.at(end) for end in stop):
                    raise ShellSyntaxError("unterminated publisher shell block")
                if consume_stop:
                    self.pop()
            return Block(tuple(items))
        finally:
            self.lexer.depth -= 1

    def atom(self) -> object:
        if self.at("!"):
            start = self.peek().offset
            prefix = self.word()
            node = self.atom()
            if not isinstance(node, Command):
                raise ShellSyntaxError("unregistered compound negation")
            return Command(node.environment, (prefix,) + node.argv, node.redirects, start, node.end, node.conditional)
        if self.at("[["):
            start = self.peek().offset
            words = [self.word()]
            while not self.at("]]"):
                token = self.pop()
                if token.kind == "word":
                    words.append(token.value)
                    if token.value.keyword("=~"):
                        while self.lexer.source[self.lexer.position:self.lexer.position + 1] in {" ", "\t"}:
                            self.lexer.position += 1
                        words.append(self.lexer._word(regex=True))
                elif token.kind == "operator" and token.value in {"&&", "||", "<", ">"}:
                    words.append(Word((Part("literal", token.value),)))
                else:
                    raise ShellSyntaxError("unregistered publisher conditional expression")
            words.append(self.word())
            return Command((), tuple(words), (), start, self.lexer.position, True)
        if self.at("if") or self.at("elif"):
            self.pop()
            condition = self.block({"then"})
            success = self.block({"else", "elif", "fi"}, consume_stop=False)
            failure = None
            if self.at("elif"):
                return If(condition, success, Block((Chain((self.atom(),)),)))
            if self.at("else"):
                self.pop()
                failure = self.block({"fi"}, consume_stop=False)
            self.require("fi")
            return If(condition, success, failure)
        if self.at("while"):
            self.pop()
            condition = self.block({"do"})
            return While(condition, self.block({"done"}))
        if self.at("for"):
            self.pop()
            variable = None
            values: list[Word] = []
            arithmetic: tuple[str, ...] = ()
            if self.peek().kind == "arithmetic":
                arithmetic = self.pop().value
            else:
                name = self.word()
                if name.literal is None or _NAME.fullmatch(name.literal) is None or not name.plain:
                    raise ShellSyntaxError("dynamic loop variable")
                variable = name.literal
                self.require("in")
                while not self.at(";") and not self.at("\n"):
                    values.append(self.word())
            if not (self.at(";") or self.at("\n")):
                raise ShellSyntaxError("missing loop separator")
            self.pop()
            self.newlines()
            self.require("do")
            return For(variable, tuple(values), arithmetic, self.block({"done"}))
        if self.at("case"):
            self.pop()
            subject = self.word()
            self.require("in")
            self.newlines()
            arms: list[CaseArm] = []
            while not self.at("esac"):
                patterns = [self.word()]
                while self.at("|"):
                    self.pop()
                    patterns.append(self.word())
                self.require(")")
                body = self.block({";;", "esac"}, consume_stop=False)
                arms.append(CaseArm(tuple(patterns), body))
                if self.at(";;"):
                    self.pop()
                self.newlines()
            self.require("esac")
            return Case(subject, tuple(arms))
        return self.command()

    def command(self) -> Command | Function:
        start = self.peek().offset
        words: list[Word] = []
        redirects: list[Redirect] = []
        while True:
            token = self.peek()
            if token.kind == "word":
                words.append(self.word())
                if len(words) == 1 and self.at("("):
                    name = words[0]
                    if name.literal is None or _NAME.fullmatch(name.literal) is None or not name.plain:
                        raise ShellSyntaxError("dynamic function declaration")
                    self.pop()
                    self.require(")")
                    self.newlines()
                    self.require("{")
                    return Function(name.literal, self.block({"}"}))
                continue
            descriptor = ""
            if token.kind == "descriptor":
                descriptor = str(self.pop().value)
                token = self.peek()
            if token.kind == "operator" and token.value in _REDIRECTS:
                operator = str(self.pop().value)
                target = self.word()
                if target.keyword("}") or target.keyword("{"):
                    raise ShellSyntaxError("missing redirection operand")
                body = self.lexer.heredoc(target) if operator == "<<" else None
                redirects.append(Redirect(descriptor, operator, target, body))
                continue
            if descriptor:
                raise ShellSyntaxError("descriptor without redirection")
            break
        if not words:
            raise ShellSyntaxError("unsupported or empty publisher command")
        count = 0
        while count < len(words) and words[count].assignment is not None:
            count += 1
        return Command(
            tuple(words[:count]), tuple(words[count:]), tuple(redirects),
            start, self.lexer.heredoc_skip[1] if self.lexer.heredoc_skip else self.peek().offset,
        )


def parse(source: str) -> Block:
    """Parse supported Bash without executing it; never return a partial tree."""
    lexer = _Lexer(source)
    parser = _Parser(lexer)
    result = parser.block(set())
    if parser.peek().kind != "end":
        raise ShellSyntaxError("unconsumed publisher shell syntax")
    return result


def command(source: str) -> Command:
    """Parse exactly one inventory command, including its nested substitutions."""
    block = parse(source)
    if (
        len(block.items) != 1
        or block.items[0].operators
        or block.items[0].background
        or len(block.items[0].nodes) != 1
        or not isinstance(block.items[0].nodes[0], Command)
    ):
        raise ShellSyntaxError("signature must describe one simple command")
    return block.items[0].nodes[0]
