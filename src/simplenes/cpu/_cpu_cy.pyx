# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""6502 CPU (Ricoh 2A03) — Cython accelerated implementation.

Independent cdef class that mirrors simplenes.cpu.cpu.CPU.
Opcode handlers remain in pure Python; all internal helpers are cpdef.
"""
cimport cython

cdef class CPUCy:
    # ==================================================================
    # Public registers — cdef public for Python handler access
    # ==================================================================
    cdef public:
        int a, x, y, sp, pc, p
        unsigned long long total_cycles
        bint halted, _page_crossed, _irq_prev
        int _last_pc, _last_opcode
        object bus, interrupts

    # CPU constants — cdef public int so handlers can do cpu.FLAG_C etc.
    cdef public int STACK_BASE, VECTOR_NMI, VECTOR_RESET, VECTOR_IRQ
    cdef public int FLAG_C, FLAG_Z, FLAG_I, FLAG_D, FLAG_B, FLAG_U, FLAG_V, FLAG_N

    # ==================================================================
    # Internal — cached callable references, trace, opcode table
    # ==================================================================
    cdef:
        object _bus_read, _bus_write
        object _trace_logger, _trace_callback
        object _opcodes

    # ==================================================================
    # __init__ / reset
    # ==================================================================

    def __init__(self, bus, interrupts):
        from simplenes.cpu.opcodes import OPCODES

        self.bus = bus
        self.interrupts = interrupts
        self._bus_read = bus.read
        self._bus_write = bus.write
        self._opcodes = OPCODES

        # CPU constants (must be cdef public int for Python handler access)
        self.STACK_BASE = 0x0100
        self.VECTOR_NMI = 0xFFFA
        self.VECTOR_RESET = 0xFFFC
        self.VECTOR_IRQ = 0xFFFE

        self.FLAG_C = 0x01
        self.FLAG_Z = 0x02
        self.FLAG_I = 0x04
        self.FLAG_D = 0x08
        self.FLAG_B = 0x10
        self.FLAG_U = 0x20
        self.FLAG_V = 0x40
        self.FLAG_N = 0x80

        # Initial register state (matches pure Python CPU)
        self.a = 0
        self.x = 0
        self.y = 0
        self.sp = 0xFD
        self.pc = 0
        self.p = self.FLAG_U | self.FLAG_I  # $24
        self.total_cycles = 0
        self.halted = False
        self._page_crossed = False
        self._irq_prev = False
        self._last_pc = 0
        self._last_opcode = 0
        self._trace_logger = None
        self._trace_callback = None

    cpdef void reset(self):
        self.sp = 0xFD
        self.p = self.FLAG_U | self.FLAG_I  # $24
        self.a = 0
        self.x = 0
        self.y = 0
        self.total_cycles = 7
        self.halted = False
        self._page_crossed = False
        self._irq_prev = False
        self._last_pc = 0
        self._last_opcode = 0
        self.pc = self._read_word(self.VECTOR_RESET)
        # Do NOT reset _bus_read / _bus_write / _opcodes / constants

    # ==================================================================
    # Bus access helpers (cpdef — called by opcode handlers)
    # ==================================================================

    cpdef int _read(self, int addr):
        return self._bus_read(addr)

    cpdef void _write(self, int addr, int value):
        self._bus_write(addr, value)

    cpdef int _read_pc(self):
        cdef int val = self._read(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return val

    cpdef int _read_word(self, int addr):
        cdef int lo = self._read(addr)
        cdef int hi = self._read((addr + 1) & 0xFFFF)
        return (hi << 8) | lo

    # ==================================================================
    # Stack helpers
    # ==================================================================

    cpdef void _push(self, int value):
        self._write(self.STACK_BASE | self.sp, value & 0xFF)
        self.sp = (self.sp - 1) & 0xFF

    cpdef int _pull(self):
        self.sp = (self.sp + 1) & 0xFF
        return self._read(self.STACK_BASE | self.sp)

    cpdef void _push_word(self, int value):
        self._push((value >> 8) & 0xFF)
        self._push(value & 0xFF)

    cpdef int _pull_word(self):
        cdef int lo = self._pull()
        cdef int hi = self._pull()
        return (hi << 8) | lo

    # ==================================================================
    # Flag helpers
    # ==================================================================

    cpdef void _set_nz(self, int value):
        value &= 0xFF
        self.p = (self.p & ~(self.FLAG_N | self.FLAG_Z)) | (value & self.FLAG_N)
        if value == 0:
            self.p |= self.FLAG_Z

    cpdef void _add_with_carry(self, int value):
        cdef int a = self.a
        cdef int c = self.p & self.FLAG_C
        cdef int temp = a + value + c
        cdef int result = temp & 0xFF
        cdef int v = (~(a ^ value) & (a ^ result)) & self.FLAG_V
        cdef int carry = 1 if temp > 0xFF else 0
        self.p = (self.p & ~(self.FLAG_V | self.FLAG_C)) | v | carry
        self.a = result
        self._set_nz(self.a)

    cpdef void _sub_with_carry(self, int value):
        self._add_with_carry(value ^ 0xFF)

    cpdef void _compare(self, int reg, int value):
        cdef int result = (reg - value) & 0xFFFF
        cdef int result_u8
        self.p = (self.p & ~(self.FLAG_C | self.FLAG_Z | self.FLAG_N))
        if reg >= value:
            self.p |= self.FLAG_C
        result_u8 = result & 0xFF
        if result_u8 == 0:
            self.p |= self.FLAG_Z
        self.p |= result_u8 & self.FLAG_N

    # ==================================================================
    # Interrupt handling
    # ==================================================================

    cpdef int _check_nmi(self):
        if self.interrupts.nmi_pending:
            self._push_word(self.pc)
            self._push((self.p & ~self.FLAG_B) | self.FLAG_U)
            self.p |= self.FLAG_I
            self.pc = self._read_word(self.VECTOR_NMI)
            self.interrupts.nmi_pending = False
            return 7
        return 0

    cpdef int _check_irq(self):
        if self.interrupts.irq_active and not (self.p & self.FLAG_I):
            self._push_word(self.pc)
            self._push((self.p & ~self.FLAG_B) | self.FLAG_U)
            self.p |= self.FLAG_I
            self.pc = self._read_word(self.VECTOR_IRQ)
            return 7
        return 0

    # ==================================================================
    # step_instruction — compiled dispatch + trace
    # ==================================================================

    cpdef int step_instruction(self):
        cdef int pc_before, opcode, cycles, intr, total
        cdef object entry

        pc_before = self.pc
        opcode = self._read_pc()
        entry = self._opcodes[opcode]

        self._last_pc = pc_before
        self._last_opcode = opcode

        # ---- trace capture (same as pure Python CPU) ----
        # Uses Python-level loop (trace is off hot path)
        if self._trace_logger is not None and self._trace_logger.enabled:
            raw_bytes = []
            for i in range(1, entry.length):
                raw_bytes.append(self._read((pc_before + i) & 0xFFFF))
            operand_bytes = tuple(raw_bytes)
            self._trace_logger.capture(
                self, pc_before, opcode, operand_bytes, entry
            )
            if self._trace_callback is not None:
                self._trace_callback(self._trace_logger.entries[-1])

        self._page_crossed = False
        cycles = entry.handler(self)

        intr = self._check_nmi()
        if intr == 0:
            intr = self._check_irq()

        total = cycles + intr
        self.total_cycles += total
        return total

    # ==================================================================
    # Trace API (same as pure Python CPU)
    # ==================================================================

    cpdef void set_trace_enabled(self, bint enabled):
        if self._trace_logger is None and enabled:
            from simplenes.cpu.cpu import CpuTraceLogger
            self._trace_logger = CpuTraceLogger()
        if self._trace_logger is not None:
            self._trace_logger.enabled = enabled

    cpdef object get_trace_logger(self):
        return self._trace_logger

    cpdef void set_trace_callback(self, callback):
        self._trace_callback = callback
        if callback is not None:
            self.set_trace_enabled(True)
