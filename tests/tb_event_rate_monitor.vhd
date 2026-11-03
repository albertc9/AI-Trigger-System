library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use work.AI_TRIGGER_PKG.all;

entity tb_event_rate_monitor is
end entity tb_event_rate_monitor;

architecture sim of tb_event_rate_monitor is
    constant CLK_PERIOD : time := 4 ns;
    constant BIN_CHUNKS : positive := 4;

    signal clk         : std_logic := '0';
    signal rst         : std_logic := '1';
    signal clear       : std_logic := '0';
    signal chunk_tick  : std_logic := '0';
    signal event_valid : std_logic := '0';
    signal event_ready : std_logic := '1';
    signal event_last  : std_logic := '0';
    signal rate_count  : unsigned(EVENT_RATE_COUNT_WIDTH - 1 downto 0);
    signal window_sec  : unsigned(EVENT_RATE_WINDOW_SEC_WIDTH - 1 downto 0);
    signal rate_high   : std_logic;
begin
    clk <= not clk after CLK_PERIOD / 2;

    dut : entity work.EVENT_RATE_MONITOR
        generic map (EVENT_RATE_BIN_CHUNKS => BIN_CHUNKS)
        port map (
            CLK                   => clk,
            RST                   => rst,
            CLEAR                 => clear,
            CHUNK_TICK            => chunk_tick,
            EVENT_VALID           => event_valid,
            EVENT_READY           => event_ready,
            EVENT_LAST            => event_last,
            EVENT_RATE_COUNT      => rate_count,
            EVENT_RATE_WINDOW_SEC => window_sec,
            EVENT_RATE_HIGH       => rate_high
        );

    stimulus : process
        procedure step is
        begin
            wait until rising_edge(clk);
            wait for 1 ps;
            chunk_tick  <= '0';
            event_valid <= '0';
            event_last  <= '0';
        end procedure;

        procedure emit_events(count : natural) is
        begin
            for idx in 1 to count loop
                event_valid <= '1';
                event_last  <= '1';
                step;
            end loop;
        end procedure;

        procedure tick_chunks(count : natural) is
        begin
            for idx in 1 to count loop
                chunk_tick <= '1';
                step;
            end loop;
        end procedure;

        procedure expect_rate(
            expected_count : natural;
            expected_sec   : natural;
            expected_high  : std_logic;
            context_text   : string
        ) is
        begin
            assert rate_count = to_unsigned(expected_count, rate_count'length)
                report context_text & ": EVENT_RATE_COUNT=" &
                       integer'image(to_integer(rate_count)) & ", expected " &
                       integer'image(expected_count)
                severity failure;
            assert window_sec = to_unsigned(expected_sec, window_sec'length)
                report context_text & ": EVENT_RATE_WINDOW_SEC=" &
                       integer'image(to_integer(window_sec)) & ", expected " &
                       integer'image(expected_sec)
                severity failure;
            assert rate_high = expected_high
                report context_text & ": unexpected EVENT_RATE_HIGH"
                severity failure;
        end procedure;
    begin
        step;
        step;
        rst <= '0';
        step;
        expect_rate(0, 0, '0', "after reset");

        emit_events(3);
        tick_chunks(BIN_CHUNKS - 1);
        expect_rate(0, 0, '0', "an open bin must not be published");

        tick_chunks(1);
        expect_rate(3, 1, '0', "first completed bin");

        emit_events(2);
        tick_chunks(1);
        expect_rate(3, 1, '0', "outputs must hold while a bin is open");
        tick_chunks(BIN_CHUNKS - 1);
        expect_rate(5, 2, '0', "second completed bin");

        for bin_idx in 3 to EVENT_RATE_BINS loop
            emit_events(2);
            tick_chunks(BIN_CHUNKS);
        end loop;
        expect_rate(129, 64, '0', "full window of 64 bins");

        emit_events(5);
        tick_chunks(BIN_CHUNKS);
        expect_rate(131, 64, '0', "the oldest bin must leave the window");

        tick_chunks(BIN_CHUNKS);
        expect_rate(129, 64, '0', "an empty bin must replace the next-oldest bin");

        -- Window now holds 62 bins of 2, one of 5 and one of 0.
        emit_events(1030);
        tick_chunks(BIN_CHUNKS);
        expect_rate(1150, 64, '1', "a bin must saturate at 1023 and raise HIGH");

        for bin_idx in 1 to EVENT_RATE_BINS - 1 loop
            tick_chunks(BIN_CHUNKS);
        end loop;
        expect_rate(1023, 64, '1', "HIGH must hold while the saturated bin is in the window");

        tick_chunks(BIN_CHUNKS);
        expect_rate(0, 64, '0', "HIGH must clear when the saturated bin leaves");

        report "tb_event_rate_monitor passed";
        std.env.stop;
        wait;
    end process;
end architecture sim;
