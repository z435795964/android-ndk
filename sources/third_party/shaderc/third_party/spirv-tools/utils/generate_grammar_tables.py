#!/usr/bin/env python
"""Generates various info tables from SPIR-V JSON grammar."""

from __future__ import print_function

import functools
import json
import os.path
import re

# Prefix for all C variables generated by this script.
PYGEN_VARIABLE_PREFIX = 'pygen_variable'

CAPABILITY_BIT_MAPPING = {}


def make_path_to_file(f):
    """Makes all ancestor directories to the given file, if they
    don't yet exist.

    Arguments:
        f: The file whose ancestor directories are to be created.
    """
    dir = os.path.dirname(os.path.abspath(f))
    if not os.path.isdir(dir):
        os.makedirs(dir)


def populate_capability_bit_mapping_dict(cap_dict):
    """Populates CAPABILITY_BIT_MAPPING.

    Arguments:
      - cap_dict: a dict containing all capability names and values
    """
    assert cap_dict['category'] == 'ValueEnum'
    assert cap_dict['kind'] == 'Capability'
    for enumerant in cap_dict['enumerants']:
        CAPABILITY_BIT_MAPPING[enumerant['enumerant']] = enumerant['value']


def compose_capability_mask(caps):
    """Returns a bit mask for a sequence of capabilities

    Arguments:
      - caps: a sequence of capability names

    Returns:
      a string containing the hexadecimal value of the bit mask
    """
    assert len(CAPABILITY_BIT_MAPPING) != 0
    bits = [CAPABILITY_BIT_MAPPING[c] for c in caps]
    caps_mask = functools.reduce(lambda m, b: m | (1 << b), bits, 0)
    return '0x{:04x}'.format(caps_mask)


def convert_operand_kind(operand_tuple):
    """Returns the corresponding operand type used in spirv-tools for
    the given operand kind and quantifier used in the JSON grammar.

    Arguments:
      - operand_tuple: a tuple of two elements:
          - operand kind: used in the JSON grammar
          - quantifier: '', '?', or '*'

    Returns:
      a string of the enumerant name in spv_operand_type_t
    """
    kind, quantifier = operand_tuple
    # The following cases are where we differ between the JSON grammar and
    # spirv-tools.
    if kind == 'IdResultType':
        kind = 'TypeId'
    elif kind == 'IdResult':
        kind = 'ResultId'
    elif kind == 'IdMemorySemantics' or kind == 'MemorySemantics':
        kind = 'MemorySemanticsId'
    elif kind == 'IdScope' or kind == 'Scope':
        kind = 'ScopeId'
    elif kind == 'IdRef':
        kind = 'Id'

    elif kind == 'ImageOperands':
        kind = 'Image'
    elif kind == 'Dim':
        kind = 'Dimensionality'
    elif kind == 'ImageFormat':
        kind = 'SamplerImageFormat'
    elif kind == 'KernelEnqueueFlags':
        kind = 'KernelEnqFlags'

    elif kind == 'LiteralExtInstInteger':
        kind = 'ExtensionInstructionNumber'
    elif kind == 'LiteralSpecConstantOpInteger':
        kind = 'SpecConstantOpNumber'
    elif kind == 'LiteralContextDependentNumber':
        kind = 'TypedLiteralNumber'

    elif kind == 'PairLiteralIntegerIdRef':
        kind = 'LiteralIntegerId'
    elif kind == 'PairIdRefLiteralInteger':
        kind = 'IdLiteralInteger'
    elif kind == 'PairIdRefIdRef':  # Used by OpPhi in the grammar
        kind = 'Id'

    if kind == 'FPRoundingMode':
        kind = 'FpRoundingMode'
    elif kind == 'FPFastMathMode':
        kind = 'FpFastMathMode'

    if quantifier == '?':
        kind = 'Optional{}'.format(kind)
    elif quantifier == '*':
        kind = 'Variable{}'.format(kind)

    return 'SPV_OPERAND_TYPE_{}'.format(
        re.sub(r'([a-z])([A-Z])', r'\1_\2', kind).upper())


class InstInitializer(object):
    """Instances holds a SPIR-V instruction suitable for printing as
    the initializer for spv_opcode_desc_t."""

    def __init__(self, opname, caps, operands):
        """Initialization.

        Arguments:
          - opname: opcode name (with the 'Op' prefix)
          - caps: a sequence of capability names required by this opcode
          - operands: a sequence of (operand-kind, operand-quantifier) tuples
        """
        assert opname.startswith('Op')
        self.opname = opname[2:]  # Remove the "Op" prefix.
        self.caps_mask = compose_capability_mask(caps)
        self.operands = [convert_operand_kind(o) for o in operands]

        operands = [o[0] for o in operands]
        self.ref_type_id = 'IdResultType' in operands
        self.def_result_id = 'IdResult' in operands

    def __str__(self):
        template = ['{{"{opname}"', 'SpvOp{opname}', '{caps_mask}',
                    '{num_operands}', '{{{operands}}}',
                    '{def_result_id}', '{ref_type_id}}}']
        return ', '.join(template).format(
            opname=self.opname,
            caps_mask=self.caps_mask,
            num_operands=len(self.operands),
            operands=', '.join(self.operands),
            def_result_id=(1 if self.def_result_id else 0),
            ref_type_id=(1 if self.ref_type_id else 0))


class ExtInstInitializer(object):
    """Instances holds a SPIR-V extended instruction suitable for printing as
    the initializer for spv_ext_inst_desc_t."""

    def __init__(self, opname, opcode, caps, operands):
        """Initialization.

        Arguments:
          - opname: opcode name
          - opcode: enumerant value for this opcode
          - caps: a sequence of capability names required by this opcode
          - operands: a sequence of (operand-kind, operand-quantifier) tuples
        """
        self.opname = opname
        self.opcode = opcode
        self.caps_mask = compose_capability_mask(caps)
        self.operands = [convert_operand_kind(o) for o in operands]
        self.operands.append('SPV_OPERAND_TYPE_NONE')

    def __str__(self):
        template = ['{{"{opname}"', '{opcode}', '{caps_mask}',
                    '{{{operands}}}}}']
        return ', '.join(template).format(
            opname=self.opname,
            opcode=self.opcode,
            caps_mask=self.caps_mask,
            operands=', '.join(self.operands))


def generate_instruction(inst, is_ext_inst):
    """Returns the C initializer for the given SPIR-V instruction.

    Arguments:
      - inst: a dict containing information about a SPIR-V instruction
      - is_ext_inst: a bool indicating whether |inst| is an extended
                     instruction.

    Returns:
      a string containing the C initializer for spv_opcode_desc_t or
      spv_ext_inst_desc_t
    """
    opname = inst.get('opname')
    opcode = inst.get('opcode')
    caps = inst.get('capabilities', [])
    operands = inst.get('operands', {})
    operands = [(o['kind'], o.get('quantifier', '')) for o in operands]

    assert opname is not None

    if is_ext_inst:
        return str(ExtInstInitializer(opname, opcode, caps, operands))
    else:
        return str(InstInitializer(opname, caps, operands))


def generate_instruction_table(inst_table, is_ext_inst):
    """Returns the info table containing all SPIR-V instructions.

    Arguments:
      - inst_table: a dict containing all SPIR-V instructions.
      - is_ext_inst: a bool indicating whether |inst_table| is for
                     an extended instruction set.
    """
    return ',\n'.join([generate_instruction(inst, is_ext_inst)
                       for inst in inst_table])


class EnumerantInitializer(object):
    """Prints an enumerant as the initializer for spv_operand_desc_t."""

    def __init__(self, enumerant, value, caps, parameters):
        """Initialization.

        Arguments:
          - enumerant: enumerant name
          - value: enumerant value
          - caps: a sequence of capability names required by this enumerant
          - parameters: a sequence of (operand-kind, operand-quantifier) tuples
        """
        self.enumerant = enumerant
        self.value = value
        self.caps_mask = compose_capability_mask(caps)
        self.parameters = [convert_operand_kind(p) for p in parameters]

    def __str__(self):
        template = ['{{"{enumerant}"', '{value}',
                    '{caps_mask}', '{{{parameters}}}}}']
        return ', '.join(template).format(
            enumerant=self.enumerant,
            value=self.value,
            caps_mask=self.caps_mask,
            parameters=', '.join(self.parameters))


def generate_enum_operand_kind_entry(entry):
    """Returns the C initializer for the given operand enum entry.

    Arguments:
      - entry: a dict containing information about an enum entry

    Returns:
      a string containing the C initializer for spv_operand_desc_t
    """
    enumerant = entry.get('enumerant')
    value = entry.get('value')
    caps = entry.get('capabilities', [])
    params = entry.get('parameters', [])
    params = [p.get('kind') for p in params]
    params = zip(params, [''] * len(params))

    assert enumerant is not None
    assert value is not None

    return str(EnumerantInitializer(enumerant, value, caps, params))


def generate_enum_operand_kind(enum):
    """Returns the C definition for the given operand kind."""
    kind = enum.get('kind')
    assert kind is not None

    name = '{}_{}Entries'.format(PYGEN_VARIABLE_PREFIX, kind)
    entries = ['  {}'.format(generate_enum_operand_kind_entry(e))
               for e in enum.get('enumerants', [])]

    template = ['static const spv_operand_desc_t {name}[] = {{',
                '{entries}', '}};']
    entries = '\n'.join(template).format(
        name=name,
        entries=',\n'.join(entries))

    return kind, name, entries


def generate_operand_kind_table(enums):
    """Returns the info table containing all SPIR-V operand kinds."""
    # We only need to output info tables for those operand kinds that are enums.
    enums = [generate_enum_operand_kind(e)
             for e in enums
             if e.get('category') in ['ValueEnum', 'BitEnum']]
    # We have three operand kinds that requires their optional counterpart to
    # exist in the operand info table.
    three_optional_enums = ['ImageOperands', 'AccessQualifier', 'MemoryAccess']
    three_optional_enums = [e for e in enums if e[0] in three_optional_enums]
    enums.extend(three_optional_enums)

    enum_kinds, enum_names, enum_entries = zip(*enums)
    # Mark the last three as optional ones.
    enum_quantifiers = [''] * (len(enums) - 3) + ['?'] * 3
    # And we don't want redefinition of them.
    enum_entries = enum_entries[:-3]
    enum_kinds = [convert_operand_kind(e)
                  for e in zip(enum_kinds, enum_quantifiers)]
    table_entries = zip(enum_kinds, enum_names, enum_names)
    table_entries = ['  {{{}, ARRAY_SIZE({}), {}}}'.format(*e)
                     for e in table_entries]

    template = [
        'static const spv_operand_desc_group_t {p}_OperandInfoTable[] = {{',
        '{enums}', '}};']
    table = '\n'.join(template).format(
        p=PYGEN_VARIABLE_PREFIX, enums=',\n'.join(table_entries))

    return '\n\n'.join(enum_entries + (table,))


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Generate SPIR-V info tables')
    parser.add_argument('--spirv-core-grammar', metavar='<path>',
                        type=str, required=True,
                        help='input JSON grammar file for core SPIR-V '
                        'instructions')
    parser.add_argument('--extinst-glsl-grammar', metavar='<path>',
                        type=str, required=False, default=None,
                        help='input JSON grammar file for GLSL extended '
                        'instruction set')
    parser.add_argument('--extinst-opencl-grammar', metavar='<path>',
                        type=str, required=False, default=None,
                        help='input JSON grammar file for OpenGL extended '
                        'instruction set')
    parser.add_argument('--core-insts-output', metavar='<path>',
                        type=str, required=False, default=None,
                        help='output file for core SPIR-V instructions')
    parser.add_argument('--glsl-insts-output', metavar='<path>',
                        type=str, required=False, default=None,
                        help='output file for GLSL extended instruction set')
    parser.add_argument('--opencl-insts-output', metavar='<path>',
                        type=str, required=False, default=None,
                        help='output file for OpenCL extended instruction set')
    parser.add_argument('--operand-kinds-output', metavar='<path>',
                        type=str, required=False, default=None,
                        help='output file for operand kinds')
    args = parser.parse_args()

    if (args.core_insts_output is None) != \
            (args.operand_kinds_output is None):
        print('error: --core-insts-output and --operand_kinds_output '
              'should be specified together.')
        exit(1)
    if (args.glsl_insts_output is None) != \
            (args.extinst_glsl_grammar is None):
        print('error: --glsl-insts-output and --extinst-glsl-grammar '
              'should be specified together.')
        exit(1)
    if (args.opencl_insts_output is None) != \
            (args.extinst_opencl_grammar is None):
        print('error: --opencl-insts-output and --extinst-opencl-grammar '
              'should be specified together.')
        exit(1)
    if all([args.core_insts_output is None,
            args.glsl_insts_output is None,
            args.opencl_insts_output is None]):
        print('error: at least one output should be specified.')
        exit(1)

    with open(args.spirv_core_grammar) as json_file:
        grammar = json.loads(json_file.read())

        # Get the dict for the Capability operand kind.
        cap_dict = [o for o in grammar['operand_kinds']
                    if o['kind'] == 'Capability']
        assert len(cap_dict) == 1
        populate_capability_bit_mapping_dict(cap_dict[0])

        if args.core_insts_output is not None:
            make_path_to_file(args.core_insts_output)
            make_path_to_file(args.operand_kinds_output)
            print(generate_instruction_table(grammar['instructions'], False),
                  file=open(args.core_insts_output, 'w'))
            print(generate_operand_kind_table(grammar['operand_kinds']),
                  file=open(args.operand_kinds_output, 'w'))

    if args.extinst_glsl_grammar is not None:
        with open(args.extinst_glsl_grammar) as json_file:
            grammar = json.loads(json_file.read())
            make_path_to_file(args.glsl_insts_output)
            print(generate_instruction_table(grammar['instructions'], True),
                  file=open(args.glsl_insts_output, 'w'))

    if args.extinst_opencl_grammar is not None:
        with open(args.extinst_opencl_grammar) as json_file:
            grammar = json.loads(json_file.read())
            make_path_to_file(args.opencl_insts_output)
            print(generate_instruction_table(grammar['instructions'], True),
                  file=open(args.opencl_insts_output, 'w'))


if __name__ == '__main__':
    main()
