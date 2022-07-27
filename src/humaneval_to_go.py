# Authored by Arjun Guha and Abhinav Jangda
# Copyright (c) 2022, Roblox Inc, Northeastern University, and University of Massachusetts Amherst
#
# This script translates problems from the OpenAI HumanEval dataset into Go.
#
# ----- Some specific issues about Go -----
#
# Due to Go's composite literals, we have to type annotate each literal with its type.
# For example, if we want a slice of ints, we have to write:
# []int{1, 2, 3}
# Or a map of string -> int:
# map[string]int{"a": 1, "b": 2}
# Therefore, this creates some slight issues with the translation, but it is
# possible to translate the code by using python's ast annotations, which is what we have done here.
#
# Unfortunately, Go does not have Union, Tuple or Optional types, so we reject those.
# For testing, Go does not have a real testing framework, therefore we ship a small testing program
# with each problem. For equality, we have exploited Go's value formatting (the "%v" format), which
# will create the same string if two values are equal. Additionally, all go test filenames have to
# end in "_test.go", otherwise go will reject them.

import re
import ast
from typing import List, Optional
from generic_translator import main

# We turn multi-line docstrings into single-line comments. This captures the
# start of the line.
DOCSTRING_LINESTART_RE = re.compile("""\n(\s+)""")


def translate_type(t):
    match t:
        case ast.Subscript(ast.Name(id), slice, ctx):
            match id:
                case "List":
                    return "[]%s" % translate_type(slice)
                case "Union":
                    raise Exception("Union unsupported")
                case "Tuple":
                    raise Exception("Tuple unsupported")
                case "Dict":
                    match slice:
                        case ast.Tuple([ast.Name(k), ast.Name(v)], _ctx):
                            key, value = translate_type(k), translate_type(v)
                            return f"map[{key}]{value}"
                        case other:
                            raise Exception(f"Bad dict: {slice}")
                case "Optional":
                    raise Exception("Optional unsupported")
                case other:
                    raise Exception(f"Bad generic {other}")
        case ast.Name("int") | "int":
            return "int"
        case ast.Name("float"):
            return "float64"
        case ast.Name("bool"):
            return "bool"
        case ast.Name("str") | "str":
            return "string"
        case None:
            raise Exception("implicitly untyped argument")
        case ast.Name("Any"):
            return "any"
        case ast.Name(x):
            raise Exception(f"unknown name {x}")
        case ast.Constant(Ellipsis):
            raise Exception("no ellipsis!!")
        case _other:
            raise Exception(f"unknown annotation: {t}")


class GoTranslator:

    # TODO: think about this carefully
    stop = ["\nfunc", "pub", "\n// ", "#[test]"]

    def __init__(self, file_ext):
        self.file_ext = file_ext
        self.type = None
        self.is_candidate_result = False

        # this is book-keeping for making the literals have types when a list/dict is empty
        self.prev_comp_types = []

    def translate_prompt(self, name: str, args: List[ast.arg], returns, description: str) -> Optional[str]:
        description = (
            "// " + re.sub(DOCSTRING_LINESTART_RE, "\n// ",
                           description.strip()) + "\n"
        )
        # Store this for later coercions on tests
        self.type = [[arg.annotation for arg in args], returns]

        def translate_arg(arg):
            arg_type = translate_type(arg.annotation)
            return arg.arg + " " + arg_type
        arg_strings = []
        retType = ""
        try:
            arg_strings = [translate_arg(arg) for arg in args]
            retType = translate_type(returns)
        except Exception as e:
            print(e)
            return None
        arg_list = ", ".join(arg_strings)
        toplevel = f"""package {name}_test

import (
    "testing"
    "fmt"
)

"""
        return f"{toplevel}{description}func {name}({arg_list}) {retType} {{\n"

    def test_suite_prefix_lines(self, entry_point) -> List[str]:
        """
        This code goes at the start of the test suite.
        """
        return [
            f"func Test{entry_point.title()}" + "(t *testing.T) {",
            f"  candidate := {entry_point}",
            "	type test struct {",
            "		actual   interface{}",
            "		expected interface{}",
            "	}",
            "   tests := []test{",
        ]

    def test_suite_suffix_lines(self) -> List[str]:
        """
        This code goes at the end of the test suite.
        """
        return [
            "   }\n",
            "	for i, tc := range tests {",
            "		t.Run(fmt.Sprintf(\"test num % d\", i), func(t *testing.T) {",
            "			if fmt.Sprintf(\"%v\", tc.actual) != fmt.Sprintf(\"%v\", tc.expected) {",
            "				t.Errorf(\"expected '%s', got '%s'\", tc.expected, tc.actual)",
            "			}",
            "		})",
            "	}",
            "}\n"
        ]

    def deep_equality(self, left: str, right: str) -> str:
        """
        All tests are assertions that compare deep equality between left and right.

        Make sure you use the right equality operator for your language. For example,
        == is the wrong operator for Java and OCaml.
        """
        return "     { actual: %s, expected: %s }," % (left, right)

    def pytype_to_gotype(self, pytype):
        # Ugh: match does not work with types
        # Only matching types that appear in the dataset
        if pytype == int:
            return "int"
        elif pytype == bool:
            return "bool"
        elif pytype == str:
            return "string"
        elif pytype == float:
            return "float64"
        elif pytype == List[int]:
            return "[]int"
        print("UNKNOWN", pytype)
        return "UNKNOWN"

    def gen_literal(self, c: bool | str | int | float | None):
        """Translate a literal expression
        c: is the literal value
        """
        if type(c) == bool:
            return str(c).lower()
        if type(c) == str:
            return f'"{c}"'
        if type(c) == None:  # this is possible, maybe we should make a box for Optional
            return "nil"
        return repr(c)

    def gen_unaryop(self, op: str, v: str) -> str:
        """Translate a unary operation (op, v)"""
        return op + v

    def gen_var(self, v: str) -> str:
        """Translate a variable with name v."""
        return v

    def gen_list(self, l: List[str]) -> str:
        """Translate a list with elements l
        A list [ x, y, z] translates to []'type'{ x, y, z }
        """
        elem_type = ""
        if len(l) == 0 and len(self.prev_comp_types) >= 1:
            elem_type = self.prev_comp_types[0]
        elif len(l) == 0:
            print("bad list, empty")
            elem_type = "int"  # a guess, but this does not happen
        else:
            elem_type = self.pytype_to_gotype(type(l[0]))

        if len(self.prev_comp_types) < 1:
            self.prev_comp_types = [elem_type]

        return f"[]{elem_type}" + "{" + ", ".join(l) + "}"

    def gen_tuple(self, t: List[str]) -> str:
        """Translate a tuple with elements t
        A tuple (x, y, z) translates to { x, y, z }
        """
        # maybe we can do a interface slice?
        raise Exception("Tuple unsupported")

    def gen_dict(self, keys: List[str], values: List[str]) -> str:
        """Translate a dictionary with keys and values
        A dictionary { "key1": val1, "key2": val2 } translates to 
            map['keyType']'valueType'{ ["key1"] = val1, ["key2"] = val2 }
        """
        keys_type = ""
        values_type = ""

        if (len(keys) == 0 or len(values) == 0) and len(self.prev_comp_types) >= 2:
            keys_type = self.prev_comp_types[0]
            values_type = self.prev_comp_types[1]
        elif len(keys) == 0 or len(values) == 0:
            print("bad dict, empty")
            # a guess, but this does not happen
            keys_type = "string"
            values_type = "int"
        else:
            keys_type = self.pytype_to_gotype(type(keys[0]))
            values_type = self.pytype_to_gotype(type(values[0]))

        if len(self.prev_comp_types) < 2:
            self.prev_comp_types = [keys_type, values_type]

        return f"map[{keys_type}]{values_type}" + "{" + ", ".join(f"{k}: {v}" for k, v in zip(keys, values)) + "}"

    def gen_call(self, func: str, args: List[str]) -> str:
        """Translate a function call `func(args)`
        A function call f(x, y, z) translates to f(x, y, z)
        """
        if func == "candidate":
            self.is_candidate_result = True
        return func + "(" + ", ".join(args) + ")"


if __name__ == "__main__":
    # NOTE: go test need to end with _test.go
    translator = GoTranslator("go_test.go")
    main(translator)