-- =============================================================================
-- EVENT_RATE_MONITOR
-- Coarse CLK_ADC-domain rate of emitted events.  One bin lasts
-- EVENT_RATE_BIN_CHUNKS committed chunks of ADC data time (default: one second
-- at 1 GSa/s).  The outputs change only when a bin completes.
-- =============================================================================

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use work.AI_TRIGGER_PKG.all;

entity EVENT_RATE_MONITOR is
    generic (
        EVENT_RATE_BIN_CHUNKS : positive := EVENT_RATE_BIN_CHUNKS_1S
    );
    port (
        CLK                   : in  std_logic;
        RST                   : in  std_logic;
        CLEAR                 : in  std_logic;
        CHUNK_TICK            : in  std_logic;

        EVENT_VALID           : in  std_logic;
        EVENT_READY           : in  std_logic;
        EVENT_LAST            : in  std_logic;

        EVENT_RATE_COUNT      : out unsigned(EVENT_RATE_COUNT_WIDTH - 1 downto 0);
        EVENT_RATE_WINDOW_SEC : out unsigned(EVENT_RATE_WINDOW_SEC_WIDTH - 1 downto 0);
        EVENT_RATE_HIGH       : out std_logic
    );
end entity EVENT_RATE_MONITOR;

architecture rtl of EVENT_RATE_MONITOR is
    signal chunk_count_r : natural range 0 to EVENT_RATE_BIN_CHUNKS - 1 := 0;
    signal bin_count_r   : unsigned(EVENT_RATE_BIN_WIDTH - 1 downto 0) :=
        (others => '0');
    signal rate_count_r  : unsigned(EVENT_RATE_COUNT_WIDTH - 1 downto 0) :=
        (others => '0');
    signal window_sec_r  : unsigned(EVENT_RATE_WINDOW_SEC_WIDTH - 1 downto 0) :=
        (others => '0');
begin
    process (CLK)
        variable bin_value : unsigned(EVENT_RATE_BIN_WIDTH - 1 downto 0);
    begin
        if rising_edge(CLK) then
            if RST = '1' then
                chunk_count_r <= 0;
                bin_count_r   <= (others => '0');
                rate_count_r  <= (others => '0');
                window_sec_r  <= (others => '0');
            else
                bin_value := bin_count_r;
                if EVENT_VALID = '1' and EVENT_READY = '1' and
                   EVENT_LAST = '1' then
                    bin_value := bin_value + 1;
                end if;

                if CHUNK_TICK = '1' and
                   chunk_count_r = EVENT_RATE_BIN_CHUNKS - 1 then
                    chunk_count_r <= 0;
                    bin_count_r   <= (others => '0');
                    rate_count_r  <= rate_count_r + resize(bin_value, rate_count_r'length);
                    window_sec_r  <= window_sec_r + 1;
                else
                    if CHUNK_TICK = '1' then
                        chunk_count_r <= chunk_count_r + 1;
                    end if;
                    bin_count_r <= bin_value;
                end if;
            end if;
        end if;
    end process;

    EVENT_RATE_COUNT      <= rate_count_r;
    EVENT_RATE_WINDOW_SEC <= window_sec_r;
    EVENT_RATE_HIGH       <= '0';
end architecture rtl;
