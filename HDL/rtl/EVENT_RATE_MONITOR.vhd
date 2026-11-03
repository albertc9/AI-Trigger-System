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
    subtype bin_count_t is unsigned(EVENT_RATE_BIN_WIDTH - 1 downto 0);
    type bin_store_t is array (0 to EVENT_RATE_BINS - 1) of bin_count_t;

    constant WINDOW_FULL : unsigned(EVENT_RATE_WINDOW_SEC_WIDTH - 1 downto 0) :=
        to_unsigned(EVENT_RATE_BINS, EVENT_RATE_WINDOW_SEC_WIDTH);

    -- Completed bins; entries are meaningful only within window_sec_r.
    signal bin_store_r   : bin_store_t := (others => (others => '0'));
    signal bin_index_r   : natural range 0 to EVENT_RATE_BINS - 1 := 0;
    signal chunk_count_r : natural range 0 to EVENT_RATE_BIN_CHUNKS - 1 := 0;
    signal bin_count_r   : bin_count_t := (others => '0');
    signal rate_count_r  : unsigned(EVENT_RATE_COUNT_WIDTH - 1 downto 0) :=
        (others => '0');
    signal window_sec_r  : unsigned(EVENT_RATE_WINDOW_SEC_WIDTH - 1 downto 0) :=
        (others => '0');

    signal event_done : std_logic;
    signal bin_close  : std_logic;
    signal bin_value  : bin_count_t;
    signal oldest_bin : bin_count_t;
begin
    event_done <= EVENT_VALID and EVENT_READY and EVENT_LAST;
    bin_close  <= '1' when CHUNK_TICK = '1' and
        chunk_count_r = EVENT_RATE_BIN_CHUNKS - 1 else '0';
    bin_value  <= bin_count_r + 1 when event_done = '1' else bin_count_r;
    oldest_bin <= bin_store_r(bin_index_r) when window_sec_r = WINDOW_FULL
        else (others => '0');

    bin_store : process (CLK)
    begin
        if rising_edge(CLK) then
            if bin_close = '1' then
                bin_store_r(bin_index_r) <= bin_value;
            end if;
        end if;
    end process;

    process (CLK)
    begin
        if rising_edge(CLK) then
            if RST = '1' then
                bin_index_r   <= 0;
                chunk_count_r <= 0;
                bin_count_r   <= (others => '0');
                rate_count_r  <= (others => '0');
                window_sec_r  <= (others => '0');
            elsif bin_close = '1' then
                chunk_count_r <= 0;
                bin_count_r   <= (others => '0');
                rate_count_r  <= rate_count_r +
                    resize(bin_value, rate_count_r'length) -
                    resize(oldest_bin, rate_count_r'length);
                if bin_index_r = EVENT_RATE_BINS - 1 then
                    bin_index_r <= 0;
                else
                    bin_index_r <= bin_index_r + 1;
                end if;
                if window_sec_r /= WINDOW_FULL then
                    window_sec_r <= window_sec_r + 1;
                end if;
            else
                if CHUNK_TICK = '1' then
                    chunk_count_r <= chunk_count_r + 1;
                end if;
                bin_count_r <= bin_value;
            end if;
        end if;
    end process;

    EVENT_RATE_COUNT      <= rate_count_r;
    EVENT_RATE_WINDOW_SEC <= window_sec_r;
    EVENT_RATE_HIGH       <= '0';
end architecture rtl;
