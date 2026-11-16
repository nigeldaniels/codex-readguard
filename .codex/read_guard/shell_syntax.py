"""Scan shell structure while consuming heredoc bodies as separate input data."""

import re
import shlex
from dataclasses import dataclass, field


TOKEN = re.compile(
    r'''(?P<space>[ \t\r\f\v]+)|(?P<continuation>\\\n)|(?P<comment>\#[^\n]*)|'''
    r'''(?P<operator><<-|[;&|<>()]+|\n)|'''
    r'''(?P<word>(?:[^\s;&|<>()'"\\]+|'[^']*'|"(?:\\.|[^"\\])*"|\\.)+)'''
)
SEPARATORS = {";", "&&", "||", "|", "|&", "&", "\n"}
OUTPUTS = {">", ">>", ">|", "&>", "&>>", "<>"}
INPUTS = {"<", ">&", "<&", "<<<"}


@dataclass
class HereDocument:
    delimiter: str
    strip_tabs: bool
    expands: bool
    descriptor: str
    body: str = ""


@dataclass
class ShellCommand:
    words: list[str] = field(default_factory=list)
    raw_words: list[str] = field(default_factory=list)
    redirects: list[str] = field(default_factory=list)
    heredocs: list[HereDocument] = field(default_factory=list)
    has_redirection: bool = False


class ShellParser:
    def __init__(self, source: str):
        self.source = source
        self.position = 0
        self.word_end = -1
        self.commands = []
        self.current = ShellCommand()
        self.pending = []

    def parse(self) -> list[ShellCommand]:
        while token := self.next_token():
            if token.lastgroup == "word":
                self.current.words.append(word_value(token))
                self.current.raw_words.append(token.group())
                self.word_end = token.end()
            else:
                self.operator(token)
        if self.pending:
            raise ValueError("Unterminated heredoc: expected a newline and delimiter")
        self.finish_command()
        return self.commands

    def operator(self, token) -> None:
        operator = token.group()
        if operator in SEPARATORS:
            self.finish_command()
            if operator == "\n":
                self.consume_bodies()
        elif operator in OUTPUTS | INPUTS | {"<<", "<<-"}:
            self.redirection(token)

    def redirection(self, token) -> None:
        operator = token.group()
        descriptor = "0"
        if self.current.words and self.word_end == token.start() and self.current.words[-1].isdigit():
            descriptor = self.current.words.pop()
            self.current.raw_words.pop()
        operand = self.next_token()
        if operand is None or operand.lastgroup != "word":
            raise ValueError(f"Missing word after shell redirection {operator!r}")
        value = word_value(operand)
        self.current.has_redirection = True
        if operator in {"<<", "<<-"}:
            document = HereDocument(value, operator == "<<-",
                                    not any(char in operand.group() for char in "'\"\\"), descriptor)
            self.current.heredocs.append(document)
            self.pending.append(document)
        elif operator in OUTPUTS or (operator == ">&" and not value.isdigit() and value != "-"):
            if not value:
                raise ValueError("Empty filename in shell redirection")
            self.current.redirects.append(value)

    def consume_bodies(self) -> None:
        # Bash reads queued heredocs in header order, after its newline. Never
        # tokenize their text as shell words: prose may contain quotes and '>\n'.
        for document in self.pending:
            lines = []
            while self.position < len(self.source):
                end = self.source.find("\n", self.position)
                end = len(self.source) if end < 0 else end + 1
                line = self.source[self.position:end]
                self.position = end
                if document.strip_tabs:
                    line = line.lstrip("\t")
                if line.removesuffix("\n") == document.delimiter:
                    document.body = "".join(lines)
                    break
                lines.append(line)
            else:
                raise ValueError(f"Unterminated heredoc: expected delimiter {document.delimiter!r}")
        self.pending.clear()

    def finish_command(self) -> None:
        if self.current.words or self.current.has_redirection:
            self.commands.append(self.current)
        self.current = ShellCommand()
        self.word_end = -1

    def next_token(self):
        while self.position < len(self.source):
            token = TOKEN.match(self.source, self.position)
            if token is None:
                raise ValueError("Unsupported or unclosed shell quoting; use literal command arguments")
            self.position = token.end()
            if token.lastgroup not in {"space", "comment", "continuation"}:
                return token
        return None


def word_value(token) -> str:
    words = shlex.split(token.group())
    if len(words) != 1:
        raise ValueError("Expected one shell word in command or redirection")
    return words[0]
