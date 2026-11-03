library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;
use work.AI_TRIGGER_PKG.all;

entity tb_multimode_event_rate is
end entity tb_multimode_event_rate;

architecture sim of tb_multimode_event_rate is
    constant BIN_CHUNKS : positive := 4;
    constant CAPTURE_BINS : positive := 3;

    signal clk : std_logic := '0';
    signal rst : std_logic := '1';
    signal data_str : std_logic := '0';
    signal adc_data4 : adc_data4_t := (others => (others => (others => '0')));
    signal trigger_mode : std_logic_vector(3 downto 0) := TRIGGER_MODE_CAPTURE_ALL;
    signal cnn_result_request : event_request_t := NULL_EVENT_REQUEST;
    signal cnn_result_ready : std_logic;
    signal live_ai_enable : std_logic;
    signal lane_busy : lane_busy_t := (others => '0');
    signal gated_lane_we : std_logic_vector(N_LANES - 1 downto 0);
    signal event_valid, event_last : std_logic;
    signal event_data : raw_adc_batch_t;
    signal event_chunk_id : chunk_id_t;
    signal event_timestamp : timestamp_t;
    signal event_offset : beat_offset_t;
    signal event_score : std_logic_vector(31 downto 0);
    signal active_mode : std_logic_vector(3 downto 0);
    signal mode_pending, invalid_mode, blanking, config_error, event_loss : std_logic;
    signal dropped_count, ring_miss_count : unsigned(31 downto 0);
    signal rate_count : unsigned(EVENT_RATE_COUNT_WIDTH - 1 downto 0);
    signal window_sec : unsigned(EVENT_RATE_WINDOW_SEC_WIDTH - 1 downto 0);
    signal rate_high : std_logic;

    signal publish_count : natural := 0;
    signal emitted_total : natural := 0;
begin
    clk <= not clk after 2 ns;

    u_dut : entity work.MULTIMODE_EVENT_PATH
        generic map (EVENT_RATE_BIN_CHUNKS => BIN_CHUNKS)
        port map (
            CLK_ADC => clk, CLK_CNN => clk, RST_ADC => rst, RST_CNN => rst,
            DATA_STR => data_str, ADC_DATA4 => adc_data4,
            TRIGGER_MODE => trigger_mode,
            FORCE_TRIGGER => '0', CNN_THRESH => (others => '0'),
            HL_THRESH => std_logic_vector(to_signed(100, 12)),
            HILO_WINDOW => std_logic_vector(to_unsigned(5, HILO_WINDOW_WIDTH)),
            COINC_WINDOW => std_logic_vector(to_unsigned(3, HILO_WINDOW_WIDTH)), BIN_THR => x"1",
            CNN_RESULT_VALID => '0', CNN_RESULT_READY => cnn_result_ready,
            CNN_RESULT_REQUEST => cnn_result_request,
            CNN_INPUT_BUSY_ADC => '0', CNN_WORK_PENDING_CNN => '0',
            CNN_CHUNK_OVERFLOW => '0', LIVE_AI_ENABLE => live_ai_enable,
            GATED_LANE_BUSY => lane_busy, GATED_LANE_WE => gated_lane_we,
            GATED_BATCH_DATA => open, GATED_START_CHUNK => open,
            GATED_START_OFFSET => open, GATED_TIMESTAMP => open,
            GATED_TRIGGER_OFFSET => open, GATED_THRESH => open,
            EVENT_VALID => event_valid, EVENT_READY => '1', EVENT_DATA => event_data,
            EVENT_LAST => event_last, EVENT_CHUNK_ID => event_chunk_id,
            EVENT_TIMESTAMP => event_timestamp, EVENT_TRIGGER_OFFSET => event_offset,
            EVENT_SCORE => event_score, ACTIVE_TRIGGER_MODE => active_mode,
            MODE_SWITCH_PENDING => mode_pending, INVALID_TRIGGER_MODE => invalid_mode,
            HILO_BLANKING => blanking, HILO_CONFIG_ERROR => config_error,
            EVENT_LOSS => event_loss, DROPPED_TRIGGER_COUNT => dropped_count,
            RING_MISS_COUNT => ring_miss_count,
            EVENT_RATE_COUNT => rate_count,
            EVENT_RATE_WINDOW_SEC => window_sec,
            EVENT_RATE_HIGH => rate_high
        );

    -- Independent observer of the public event stream.  Every published rate
    -- must equal the Emitted Events seen since the current mode started.
    observer : process
        variable since_mode_start : natural := 0;
        variable last_mode : std_logic_vector(3 downto 0) := "1111";
        variable last_sec : unsigned(EVENT_RATE_WINDOW_SEC_WIDTH - 1 downto 0) :=
            (others => '0');
    begin
        wait until rising_edge(clk);
        if rst = '0' and event_valid = '1' and event_last = '1' then
            since_mode_start := since_mode_start + 1;
            emitted_total <= emitted_total + 1;
        end if;
        wait for 1 ps;
        if active_mode /= last_mode then
            last_mode := active_mode;
            since_mode_start := 0;
        end if;
        if window_sec /= last_sec then
            if window_sec /= 0 then
                assert rate_count = to_unsigned(since_mode_start, rate_count'length)
                    report "published rate " & integer'image(to_integer(rate_count)) &
                           " differs from observed Emitted Events " &
                           integer'image(since_mode_start)
                    severity failure;
                assert rate_high = '0'
                    report "unsaturated Capture-All bins raised EVENT_RATE_HIGH"
                    severity failure;
                publish_count <= publish_count + 1;
            end if;
            last_sec := window_sec;
        end if;
    end process;

    stimulus : process
        variable chunk_value : natural := 0;

        procedure stream_chunks(count : natural) is
        begin
            for idx in 1 to count loop
                for beat_value in 0 to N_BATCHES - 1 loop
                    for ch in 0 to N_ADC_CH - 1 loop
                        for sample_idx in 0 to N_BATCH_S - 1 loop
                            adc_data4(ch)(sample_idx) <= std_logic_vector(to_unsigned(
                                (chunk_value * 64 + beat_value + ch * 256) mod 4096, 12));
                        end loop;
                    end loop;
                    data_str <= '1';
                    wait until rising_edge(clk);
                end loop;
                chunk_value := chunk_value + 1;
            end loop;
            data_str <= '0';
        end procedure;

        procedure idle(cycles : natural) is
        begin
            for idx in 1 to cycles loop
                wait until rising_edge(clk);
            end loop;
        end procedure;
    begin
        wait until rising_edge(clk);
        rst <= '0';

        -- Chunk 0 starts Capture-All; the next CAPTURE_BINS bins follow.
        stream_chunks(1 + CAPTURE_BINS * BIN_CHUNKS);
        idle(4 * N_BATCHES);
        assert active_mode = TRIGGER_MODE_CAPTURE_ALL
            report "Capture-All did not start" severity failure;
        assert window_sec = to_unsigned(CAPTURE_BINS, window_sec'length)
            report "window must hold exactly the completed Capture-All bins"
            severity failure;
        assert publish_count = CAPTURE_BINS
            report "each completed bin must publish once" severity failure;
        assert rate_count > 0 and
               rate_count <= to_unsigned(CAPTURE_BINS * BIN_CHUNKS, rate_count'length)
            report "Capture-All rate outside one event per chunk" severity failure;
        assert emitted_total = CAPTURE_BINS * BIN_CHUNKS
            report "Capture-All did not emit one event per streamed chunk"
            severity failure;

        -- A runtime mode change clears the rate when the new mode starts.
        trigger_mode <= TRIGGER_MODE_EXTERNAL;
        while active_mode /= TRIGGER_MODE_EXTERNAL loop
            stream_chunks(1);
            idle(8);
        end loop;
        assert rate_count = 0 and window_sec = 0 and rate_high = '0'
            report "mode start must clear the event rate" severity failure;

        stream_chunks(BIN_CHUNKS);
        idle(8);
        assert window_sec = 1 and rate_count = 0
            report "External mode without requests must publish an empty bin"
            severity failure;
        assert event_loss = '0' and ring_miss_count = 0
            report "event rate scenario unexpectedly reported event loss"
            severity failure;

        report "tb_multimode_event_rate passed";
        stop;
        wait;
    end process;
end architecture sim;
