// ignore_for_file: non_constant_identifier_names

DateTime? _parseDate(dynamic v) =>
    v == null ? null : DateTime.tryParse(v.toString())?.toLocal();

/// Player info with name and XUID
class PlayerInfo {
  final String name;
  final String? xuid;

  PlayerInfo({
    required this.name,
    this.xuid,
  });

  factory PlayerInfo.fromJson(Map<String, dynamic> json) {
    return PlayerInfo(
      name: json['name'] as String,
      xuid: json['xuid'] as String?,
    );
  }
}

/// Mirrors BanOut schema from backend.
class Ban {
  final String ban_id;
  final String server_id;
  final String xuid;
  final String player_name;
  final String? reason;
  final bool active;
  final DateTime banned_at;
  final DateTime? expires_at;
  final DateTime? unbanned_at;
  final String created_by_user_id;
  final String? unbanned_by_user_id;

  Ban({
    required this.ban_id,
    required this.server_id,
    required this.xuid,
    required this.player_name,
    this.reason,
    required this.active,
    required this.banned_at,
    this.expires_at,
    this.unbanned_at,
    required this.created_by_user_id,
    this.unbanned_by_user_id,
  });

  factory Ban.fromJson(Map<String, dynamic> json) {
    return Ban(
      ban_id: json['ban_id'] as String,
      server_id: json['server_id'] as String,
      xuid: json['xuid'] as String,
      player_name: json['player_name'] as String,
      reason: json['reason'] as String?,
      active: json['active'] as bool,
      banned_at: _parseDate(json['banned_at'])!,
      expires_at: _parseDate(json['expires_at']),
      unbanned_at: _parseDate(json['unbanned_at']),
      created_by_user_id: json['created_by_user_id'] as String,
      unbanned_by_user_id: json['unbanned_by_user_id'] as String?,
    );
  }

  bool get isPermanent => expires_at == null;

  bool get isExpired => !active && unbanned_at != null;

  String get durationDisplay {
    if (isPermanent) return 'Permanent';
    if (expires_at == null) return 'Unknown';
    final now = DateTime.now();
    final diff = expires_at!.difference(now);
    if (diff.isNegative) return 'Expired';
    final days = diff.inDays;
    final hours = diff.inHours % 24;
    final minutes = diff.inMinutes % 60;
    if (days > 0) return '$days days';
    if (hours > 0) return '$hours hours';
    return '$minutes minutes';
  }
}

/// Mirrors BanListOut schema from backend.
class BanList {
  final List<Ban> items;

  BanList({required this.items});

  factory BanList.fromJson(Map<String, dynamic> json) {
    final items = (json['items'] as List? ?? [])
        .map((e) => Ban.fromJson(e as Map<String, dynamic>))
        .toList();
    return BanList(items: items);
  }
}

/// Mirrors BanCheckResponse schema from backend.
class BanCheckResponse {
  final bool banned;
  final String? ban_id;
  final String? xuid;
  final String? player_name;
  final String? reason;
  final DateTime? expires_at;
  final bool? active;

  BanCheckResponse({
    required this.banned,
    this.ban_id,
    this.xuid,
    this.player_name,
    this.reason,
    this.expires_at,
    this.active,
  });

  factory BanCheckResponse.fromJson(Map<String, dynamic> json) {
    return BanCheckResponse(
      banned: json['banned'] as bool,
      ban_id: json['ban_id'] as String?,
      xuid: json['xuid'] as String?,
      player_name: json['player_name'] as String?,
      reason: json['reason'] as String?,
      expires_at: _parseDate(json['expires_at']),
      active: json['active'] as bool?,
    );
  }
}
