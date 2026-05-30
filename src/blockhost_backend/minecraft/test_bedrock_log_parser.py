from blockhost_backend.minecraft.bedrock_log_parser import BedrockLogParser


def test_player_connect_disconnect() -> None:
    parser = BedrockLogParser()
    parser.parse_line("[INFO] Server started.")
    parser.parse_line("Player connected: Steve, xuid: 12345")
    assert parser.stats.players_online == 1
    assert parser.stats.online_players == ["Steve"]

    parser.parse_line("Player disconnected: Steve, xuid: 12345, platform: Android")
    assert parser.stats.players_online == 0
    assert parser.stats.online_players == []


def test_tick_parsing() -> None:
    parser = BedrockLogParser()
    parser.parse_line("Average tick time: 50.0 ms")
    assert parser.stats.tick_ms == 50.0
    assert parser.stats.tps == 20.0
