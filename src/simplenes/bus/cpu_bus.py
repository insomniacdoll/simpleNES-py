"""CPU address space (64 KiB)."""


class CPUBus:
    """
    $0000-$07FF  : 2 KiB internal RAM
    $0800-$1FFF  : RAM mirrors
    $2000-$2007  : PPU registers
    $2008-$3FFF  : PPU register mirrors
    $4000-$4013  : APU registers
    $4014        : OAM DMA
    $4015        : APU status
    $4016        : Controller 1
    $4017        : Controller 2
    $4018-$401F  : disabled
    $4020-$5FFF  : Cartridge expansion
    $6000-$7FFF  : PRG RAM
    $8000-$FFFF  : PRG ROM / Mapper
    """

    __slots__ = (
        "_ram",
        "_ppu",
        "_apu",
        "_mapper",
        "_controller1",
        "_controller2",
        "_oam_dma_state",
    )

    def __init__(self, ppu, apu, mapper, controller1, controller2, oam_dma_state):
        self._ram = bytearray(2048)
        self._ppu = ppu
        self._apu = apu
        self._mapper = mapper
        self._controller1 = controller1
        self._controller2 = controller2
        self._oam_dma_state = oam_dma_state

    def read(self, address: int) -> int:
        """Read one byte from CPU address space."""
        address &= 0xFFFF

        if address < 0x2000:
            return self._ram[address & 0x07FF]

        if address < 0x4000:
            return self._ppu.read_register(0x2000 | (address & 0x0007))

        if address == 0x4015:
            return self._apu.read_status()

        if address == 0x4016:
            return self._controller1.read()

        if address == 0x4017:
            return self._controller2.read()

        if address >= 0x4020:
            return self._mapper.cpu_read(address)

        return 0

    def write(self, address: int, value: int) -> None:
        """Write one byte to CPU address space."""
        address &= 0xFFFF
        value &= 0xFF

        if address < 0x2000:
            self._ram[address & 0x07FF] = value
            return

        if address < 0x4000:
            self._ppu.write_register(0x2000 | (address & 0x0007), value)
            return

        if address < 0x4014:
            self._apu.write_register(address, value)
            return

        if address == 0x4014:
            self._oam_dma_state.trigger(value)
            return

        if address == 0x4015:
            self._apu.write_register(address, value)
            return

        if address == 0x4016:
            self._controller1.write_strobe(value)
            self._controller2.write_strobe(value)
            return

        if address == 0x4017:
            self._apu.write_register(address, value)
            return

        if address >= 0x4020:
            self._mapper.cpu_write(address, value)
            return
