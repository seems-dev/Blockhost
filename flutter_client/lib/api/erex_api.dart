import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/backup_models.dart';
import '../models/ban_models.dart';
import '../models/mod_models.dart';

class ApiException implements Exception {
  ApiException(this.message);
  final String message;

  @override
  String toString() => message;
}

/// Thrown when a login attempt fails because the email has not been verified.
/// The backend auto-resends the OTP, so the UI should show the verification flow.
class EmailNotVerifiedException implements Exception {
  EmailNotVerifiedException(this.email, this.message);
  final String email;
  final String message;

  @override
  String toString() => message;
}

class ErexApi {
  ErexApi({required this.baseUrl, required this.accessToken});

  final String baseUrl;
  final String? accessToken;

  Uri _u(String path) => Uri.parse('$baseUrl$path');

  Uri getConsoleWebSocketUri(String serverId) {
    final wsBase = baseUrl.replaceFirst('http', 'ws');
    final uri = Uri.parse('$wsBase/api/servers/$serverId/console/ws');
    if (accessToken != null && accessToken!.isNotEmpty) {
      return uri.replace(queryParameters: {'token': accessToken});
    }
    return uri;
  }

  Map<String, String> _headers({bool json = true, bool auth = false}) {
    final headers = <String, String>{};
    if (json) headers['Content-Type'] = 'application/json';
    if (auth) {
      final token = accessToken;
      if (token != null && token.isNotEmpty)
        headers['Authorization'] = 'Bearer $token';
    }
    return headers;
  }

  Future<Map<String, dynamic>> signup({
    required String email,
    required String password,
    required String nickname,
  }) async {
    final res = await http.post(
      _u('/api/auth/signup'),
      headers: _headers(),
      body: jsonEncode(
          {'email': email, 'password': password, 'nickname': nickname}),
    );
    final body = _decodeJson(res.body);
    if (res.statusCode != 201) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> verifyEmail({
    required String email,
    required String otp,
  }) async {
    final res = await http.post(
      _u('/api/auth/verify-email'),
      headers: _headers(),
      body: jsonEncode({'email': email, 'otp': otp}),
    );
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> resendVerification({
    required String email,
  }) async {
    final res = await http.post(
      _u('/api/auth/resend-verification'),
      headers: _headers(),
      body: jsonEncode({'email': email}),
    );
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> login(
      {required String email, required String password}) async {
    final res = await http.post(
      _u('/api/auth/login'),
      headers: _headers(),
      body: jsonEncode({'email': email, 'password': password}),
    );
    final body = _decodeJson(res.body);
    // Detect the "verification_required" 403 and throw a specific exception
    // so the UI can redirect to the OTP screen instead of showing a dead-end.
    if (res.statusCode == 403) {
      final detail = body['detail'];
      if (detail is Map && detail['status'] == 'verification_required') {
        throw EmailNotVerifiedException(
          detail['email']?.toString() ?? email,
          detail['message']?.toString() ?? 'Email not verified',
        );
      }
    }
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<void> logout({required String refreshToken}) async {
    final res = await http.post(
      _u('/api/auth/logout'),
      headers: _headers(),
      body: jsonEncode({'refresh_token': refreshToken}),
    );
    if (res.statusCode != 200) {
      final body = _decodeJson(res.body);
      throw ApiException(_err(body, res.statusCode));
    }
  }

  Future<void> logoutAll() async {
    final res = await http.post(
      _u('/api/auth/logout-all'),
      headers: _headers(auth: true),
    );
    if (res.statusCode != 200) {
      final body = _decodeJson(res.body);
      throw ApiException(_err(body, res.statusCode));
    }
  }

  Future<String> refreshAuthToken({required String refreshToken}) async {
    final res = await http.post(
      _u('/api/auth/refresh'),
      headers: _headers(auth: false),
      body: jsonEncode({'refresh_token': refreshToken}),
    );
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body['access_token'] as String;
  }

  Future<Map<String, dynamic>> googleLogin({required String idToken}) async {
    final res = await http.post(
      _u('/api/auth/google'),
      headers: _headers(),
      body: jsonEncode({'id_token': idToken}),
    );
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> createServer(
      {required String worldName, String tier = 'premium'}) async {
    return createServerWithConfig(worldName: worldName, tier: tier);
  }

  Future<Map<String, dynamic>> createServerWithConfig({
    required String worldName,
    String tier = 'premium',
    String flavor = 'bedrock',
    String? mcVersion,
    Map<String, dynamic>? config,
  }) async {
    final res = await http.post(
      _u('/api/servers'),
      headers: _headers(auth: true),
      body: jsonEncode({
        'world_name': worldName,
        'tier': tier,
        'flavor': flavor,
        if (mcVersion != null) 'mc_version': mcVersion,
        if (config != null) 'config': config
      }),
    );
    final body = _decodeJson(res.body);
    if (res.statusCode != 201) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<List<dynamic>> listPlans() async {
    final res = await http.get(_u('/api/billing/plans'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    if (body is List) return body;
    throw ApiException('Unexpected response');
  }

  Future<Map<String, dynamic>> createUpgradeOrder({
    required String serverId,
    required String targetPlanId,
  }) async {
    final res = await http.post(
      _u('/api/billing/upgrade'),
      headers: _headers(auth: true),
      body: jsonEncode({
        'server_id': serverId,
        'target_plan_id': targetPlanId,
      }),
    );
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> verifyPayment({
    required String providerOrderId,
    required String providerPaymentId,
    required String signature,
  }) async {
    final res = await http.post(
      _u('/api/billing/verify'),
      headers: _headers(auth: true),
      body: jsonEncode({
        'provider_order_id': providerOrderId,
        'provider_payment_id': providerPaymentId,
        'signature': signature,
      }),
    );
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<List<dynamic>> listServers() async {
    final res = await http.get(_u('/api/servers'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    if (body is List) return body;
    throw ApiException('Unexpected response');
  }

  Future<Map<String, dynamic>> getServer(String id) async {
    final res = await http.get(_u('/api/servers/$id'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> getProvisionStatus(String id) async {
    final res = await http.get(_u('/api/servers/$id/provision-status'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> reprovisionServer(String id) async {
    final res = await http.post(_u('/api/servers/$id/reprovision'),
        headers: _headers(auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> switchSoftware(String id, String targetFlavor, String targetMcVersion) async {
    final res = await http.post(
      _u('/api/servers/$id/switch-software'),
      headers: _headers(auth: true),
      body: jsonEncode({
        'target_flavor': targetFlavor,
        'target_mc_version': targetMcVersion,
      }),
    );
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> startServer(String id) async {
    final res = await http.post(_u('/api/servers/$id/start'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> stopServer(String id) async {
    final res = await http.post(_u('/api/servers/$id/stop'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> toggleServer(String id) async {
    final res = await http.post(_u('/api/servers/$id/toggle'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> getServerStats(String id) async {
    final res = await http.get(_u('/api/servers/$id/stats'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  /// Get list of online players on the server with their XUIDs
  Future<List<PlayerInfo>> getOnlinePlayers(String serverId) async {
    try {
      final stats = await getServerStats(serverId);
      final playersList = stats['online_players_list'] as List? ?? [];
      return playersList.map((p) {
        if (p is Map<String, dynamic>) return PlayerInfo.fromJson(p);
        if (p is Map) {
          return PlayerInfo(
            name: p['name']?.toString() ?? '',
            xuid: p['xuid']?.toString(),
          );
        }
        return PlayerInfo(name: p?.toString() ?? '');
      }).where((info) => info.name.isNotEmpty).toList();
    } catch (e) {
      return [];
    }
  }

  Future<List<String>> getServerLogs(String id, {int tail = 100}) async {
    final res = await http.get(
      _u('/api/servers/$id/logs?tail=$tail'),
      headers: _headers(json: false, auth: true),
    );
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    if (body is List) return body.map((e) => e.toString()).toList();
    throw ApiException('Unexpected response');
  }

  Future<Map<String, dynamic>> getServerConfig(String id) async {
    final res = await http.get(_u('/api/servers/$id/config'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> getServerSubscription(String id) async {
    final res = await http.get(_u('/api/billing/subscriptions/$id'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> updateServerConfig(
      String id, Map<String, dynamic> config) async {
    final res = await http.patch(
      _u('/api/servers/$id/config'),
      headers: _headers(auth: true),
      body: jsonEncode({'config': config}),
    );
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    return body;
  }

  Future<Map<String, dynamic>> getBedrockVersions() async {
    final res = await http.get(_u('/api/versions/catalog/remote'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    if (body is Map<String, dynamic>) return body;
    throw ApiException('Unexpected response');
  }

  Future<Map<String, dynamic>> getJavaVersionsCatalog() async {
    final res = await http.get(_u('/api/versions/java-catalog'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    if (body is Map<String, dynamic>) return body;
    throw ApiException('Unexpected response');
  }

  Future<Map<String, dynamic>> downloadVersion(String version) async {
    final res = await http.post(_u('/api/versions/$version/download'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    if (body is Map<String, dynamic>) return body;
    throw ApiException('Unexpected response');
  }

  Future<ModCapability> getModCapability(String serverId) async {
    final res = await http.get(
      _u('/api/servers/$serverId/mods/capability'),
      headers: _headers(auth: true),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return ModCapability.fromJson(decoded as Map<String, dynamic>);
  }

  Future<List<ModSearchResult>> searchMods(String serverId, String query) async {
    final uri = _u('/api/servers/$serverId/mods/search').replace(
      queryParameters: {'q': query, 'limit': '20'},
    );
    final res = await http.get(uri, headers: _headers(auth: true));
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    if (decoded is Map<String, dynamic>) {
      final hits = decoded['hits'] as List? ?? [];
      return hits
          .map((item) => ModSearchResult.fromJson(item as Map<String, dynamic>))
          .toList();
    }
    throw ApiException('Unexpected response');
  }

  Future<List<InstalledMod>> listInstalledMods(String serverId) async {
    final res = await http.get(
      _u('/api/servers/$serverId/mods'),
      headers: _headers(auth: true),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    if (decoded is Map<String, dynamic>) {
      final files = decoded['files'] as List? ?? [];
      return files
          .map((item) => InstalledMod(filename: item.toString()))
          .toList();
    }
    throw ApiException('Unexpected response');
  }

  Future<Map<String, dynamic>> installMod(
    String serverId,
    ModInstallRequest request,
  ) async {
    final res = await http.post(
      _u('/api/servers/$serverId/mods/install'),
      headers: _headers(auth: true),
      body: jsonEncode(request.toJson()),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return decoded as Map<String, dynamic>;
  }

  Future<void> deleteMod(String serverId, String filename) async {
    final res = await http.delete(
      _u('/api/servers/$serverId/mods/$filename'),
      headers: _headers(auth: true),
    );
    if (res.statusCode != 204) {
      final decoded = _decodeJson(res.body);
      throw ApiException(_err(decoded, res.statusCode));
    }
  }

  Future<ModProjectDetails> getModDetails(String serverId, String projectId) async {
    final res = await http.get(
      _u('/api/servers/$serverId/mods/project/$projectId'),
      headers: _headers(auth: true),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return ModProjectDetails.fromJson(decoded as Map<String, dynamic>);
  }

  Future<Map<String, dynamic>> _postControl(
      String path, Map<String, dynamic> body) async {
    final res = await http.post(_u(path),
        headers: _headers(auth: true), body: jsonEncode(body));
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200)
      throw ApiException(_err(decoded, res.statusCode));
    return decoded as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> teleportPlayer(String id,
          {required String player,
          required double x,
          required double y,
          required double z}) =>
      _postControl('/api/servers/$id/teleport',
          {'player': player, 'x': x, 'y': y, 'z': z});

  Future<Map<String, dynamic>> clearInventory(String id,
          {required String player}) =>
      _postControl('/api/servers/$id/clear-inventory', {'player': player});

  Future<Map<String, dynamic>> banPlayer(String id, {required String player}) =>
      _postControl('/api/servers/$id/ban', {'player': player});

  Future<Map<String, dynamic>> kickPlayer(String id,
          {required String player}) =>
      _postControl('/api/servers/$id/kick', {'player': player});

  Future<Map<String, dynamic>> opPlayer(String id,
          {required String player, bool grant = true}) =>
      _postControl('/api/servers/$id/op', {'player': player, 'grant': grant});

  Future<Map<String, dynamic>> setGamemode(String id,
          {required String player, required String mode}) =>
      _postControl(
          '/api/servers/$id/gamemode', {'player': player, 'mode': mode});

  Future<Map<String, dynamic>> setTime(String id, {required String value}) =>
      _postControl('/api/servers/$id/time', {'value': value});

  Future<Map<String, dynamic>> setWeather(String id,
          {required String weather}) =>
      _postControl('/api/servers/$id/weather', {'weather': weather});

  Future<Map<String, dynamic>> sayMessage(String id,
          {required String message}) =>
      _postControl('/api/servers/$id/say', {'message': message});

  Future<List<String>> getBlocklist(String id) async {
    final res = await http.get(_u('/api/servers/$id/blocklist'),
        headers: _headers(json: false, auth: true));
    final body = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(body, res.statusCode));
    if (body is Map && body['players'] is List) {
      return (body['players'] as List).map((e) => e.toString()).toList();
    }
    throw ApiException('Unexpected response');
  }

  // ─── Ban management endpoints ──────────────────────────────────────────────

  /// GET /api/servers/{id}/bans  → BanListOut
  Future<BanList> listBans(String serverId) async {
    final res = await http.get(
      _u('/api/servers/$serverId/bans'),
      headers: _headers(json: false, auth: true),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return BanList.fromJson(decoded as Map<String, dynamic>);
  }

  /// GET /api/servers/{id}/bans/history  → BanListOut
  Future<BanList> listBanHistory(String serverId) async {
    final res = await http.get(
      _u('/api/servers/$serverId/bans/history'),
      headers: _headers(json: false, auth: true),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return BanList.fromJson(decoded as Map<String, dynamic>);
  }

  /// GET /api/servers/{id}/bans/check?xuid={xuid}  → BanCheckResponse
  Future<BanCheckResponse> checkBan(String serverId, String xuid) async {
    final uri = _u('/api/servers/$serverId/bans/check')
        .replace(queryParameters: {'xuid': xuid});
    final res = await http.get(uri, headers: _headers(json: false, auth: true));
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return BanCheckResponse.fromJson(decoded as Map<String, dynamic>);
  }

  /// POST /api/servers/{id}/bans  → BanOut
  Future<Ban> createBan(
    String serverId, {
    required String xuid,
    required String playerName,
    String? reason,
    int? durationSeconds,
    DateTime? expiresAt,
  }) async {
    final body = <String, dynamic>{
      'xuid': xuid,
      'player_name': playerName,
    };
    if (reason != null && reason.isNotEmpty) body['reason'] = reason;
    if (durationSeconds != null) body['duration_seconds'] = durationSeconds;
    if (expiresAt != null) body['expires_at'] = expiresAt.toIso8601String();

    final res = await http.post(
      _u('/api/servers/$serverId/bans'),
      headers: _headers(auth: true),
      body: jsonEncode(body),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200 && res.statusCode != 201)
      throw ApiException(_err(decoded, res.statusCode));
    return Ban.fromJson(decoded as Map<String, dynamic>);
  }

  /// DELETE /api/servers/{id}/bans/{banId}  → BanOut
  Future<Ban> deleteBan(String serverId, String banId) async {
    final res = await http.delete(
      _u('/api/servers/$serverId/bans/$banId'),
      headers: _headers(auth: true),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200)
      throw ApiException(_err(decoded, res.statusCode));
    return Ban.fromJson(decoded as Map<String, dynamic>);
  }

  // ─── Backup endpoints ──────────────────────────────────────────────────────

  /// POST /api/servers/{id}/backups  → 202 BackupJobOut
  Future<BackupJob> createBackup(
    String serverId, {
    String? name,
    String? description,
  }) async {
    final body = <String, dynamic>{};
    if (name != null && name.isNotEmpty) body['name'] = name;
    if (description != null && description.isNotEmpty)
      body['description'] = description;
    final res = await http.post(
      _u('/api/servers/$serverId/backups'),
      headers: _headers(auth: true),
      body: jsonEncode(body),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 202 && res.statusCode != 200)
      throw ApiException(_err(decoded, res.statusCode));
    return BackupJob.fromJson(decoded as Map<String, dynamic>);
  }

  /// GET /api/servers/{id}/backups  → BackupListOut
  Future<List<Backup>> listBackups(String serverId) async {
    final res = await http.get(
      _u('/api/servers/$serverId/backups?limit=50'),
      headers: _headers(json: false, auth: true),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    final items = (decoded as Map<String, dynamic>)['items'] as List? ?? [];
    return items
        .map((e) => Backup.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// GET /api/servers/{id}/backups/{backupId}
  Future<Backup> getBackup(String serverId, String backupId) async {
    final res = await http.get(
      _u('/api/servers/$serverId/backups/$backupId'),
      headers: _headers(json: false, auth: true),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return Backup.fromJson(decoded as Map<String, dynamic>);
  }

  /// DELETE /api/servers/{id}/backups/{backupId}  → 204
  Future<void> deleteBackup(
    String serverId,
    String backupId, {
    bool deleteEvenIfPinned = false,
  }) async {
    final res = await http.delete(
      _u('/api/servers/$serverId/backups/$backupId'),
      headers: _headers(auth: true),
      body: jsonEncode({'delete_even_if_pinned': deleteEvenIfPinned}),
    );
    if (res.statusCode != 204) {
      final decoded = _decodeJson(res.body);
      throw ApiException(_err(decoded, res.statusCode));
    }
  }

  /// Returns the download URL (not the bytes) – caller opens via url_launcher.
  String backupDownloadUrl(String serverId, String backupId) =>
      '$baseUrl/api/servers/$serverId/backups/$backupId/download';

  /// Streams the backup file as bytes for in-app downloading.
  Future<http.Response> downloadBackupBytes(
      String serverId, String backupId) async {
    final res = await http.get(
      _u('/api/servers/$serverId/backups/$backupId/download'),
      headers: _headers(json: false, auth: true),
    );
    if (res.statusCode != 200) {
      final decoded = _decodeJson(res.body);
      throw ApiException(_err(decoded, res.statusCode));
    }
    return res;
  }

  // ─── Backup job status ─────────────────────────────────────────────────────

  /// GET /api/servers/{id}/backup-jobs/{jobId}
  Future<BackupJob> getBackupJob(String serverId, String jobId) async {
    final res = await http.get(
      _u('/api/servers/$serverId/backup-jobs/$jobId'),
      headers: _headers(json: false, auth: true),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return BackupJob.fromJson(decoded as Map<String, dynamic>);
  }

  // ─── Restore endpoints ─────────────────────────────────────────────────────

  /// POST /api/servers/{id}/restores  → 202 RestoreJobOut
  Future<RestoreJob> createRestore(
    String serverId, {
    required String backupId,
    bool restartAfterRestore = true,
    bool rollbackEnabled = true,
  }) async {
    final res = await http.post(
      _u('/api/servers/$serverId/restores'),
      headers: _headers(auth: true),
      body: jsonEncode({
        'backup_id': backupId,
        'restart_after_restore': restartAfterRestore,
        'rollback_enabled': rollbackEnabled,
        'confirm': 'RESTORE',
      }),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 202 && res.statusCode != 200)
      throw ApiException(_err(decoded, res.statusCode));
    return RestoreJob.fromJson(decoded as Map<String, dynamic>);
  }

  /// GET /api/servers/{id}/restore-jobs/{jobId}
  Future<RestoreJob> getRestoreJob(String serverId, String jobId) async {
    final res = await http.get(
      _u('/api/servers/$serverId/restore-jobs/$jobId'),
      headers: _headers(json: false, auth: true),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return RestoreJob.fromJson(decoded as Map<String, dynamic>);
  }

  // ─── Backup schedule endpoints ─────────────────────────────────────────────

  /// GET /api/servers/{id}/backup-schedule
  Future<BackupSchedule?> getBackupSchedule(String serverId) async {
    final res = await http.get(
      _u('/api/servers/$serverId/backup-schedule'),
      headers: _headers(json: false, auth: true),
    );
    if (res.statusCode == 404) return null;
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return BackupSchedule.fromJson(decoded as Map<String, dynamic>);
  }

  /// PUT /api/servers/{id}/backup-schedule
  Future<BackupSchedule> upsertBackupSchedule(
    String serverId, {
    required bool enabled,
    required int intervalMinutes,
    required int retentionCount,
    int? retentionDays,
    bool skipIfServerOffline = false,
    bool deferIfJobActive = true,
  }) async {
    final res = await http.put(
      _u('/api/servers/$serverId/backup-schedule'),
      headers: _headers(auth: true),
      body: jsonEncode({
        'enabled': enabled,
        'interval_minutes': intervalMinutes,
        'retention_count': retentionCount,
        if (retentionDays != null) 'retention_days': retentionDays,
        'skip_if_server_offline': skipIfServerOffline,
        'defer_if_job_active': deferIfJobActive,
      }),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return BackupSchedule.fromJson(decoded as Map<String, dynamic>);
  }

  /// DELETE /api/servers/{id}/backup-schedule  → 204
  Future<void> deleteBackupSchedule(String serverId) async {
    final res = await http.delete(
      _u('/api/servers/$serverId/backup-schedule'),
      headers: _headers(json: false, auth: true),
    );
    if (res.statusCode != 204) {
      final decoded = _decodeJson(res.body);
      throw ApiException(_err(decoded, res.statusCode));
    }
  }

  // ─── Retention ─────────────────────────────────────────────────────────────

  /// POST /api/servers/{id}/backups/retention/apply  → 204
  Future<void> applyRetentionNow(String serverId) async {
    final res = await http.post(
      _u('/api/servers/$serverId/backups/retention/apply'),
      headers: _headers(json: false, auth: true),
    );
    if (res.statusCode != 204) {
      final decoded = _decodeJson(res.body);
      throw ApiException(_err(decoded, res.statusCode));
    }
  }

  dynamic _decodeJson(String raw) {
    if (raw.isEmpty) return {};
    return jsonDecode(raw);
  }

  String _err(dynamic decoded, int status) {
    if (decoded is Map && decoded['detail'] is String)
      return '${decoded['detail']} ($status)';
    return 'Request failed ($status)';
  }

  // ─── Server properties endpoints ───────────────────────────────────────────

  Future<Map<String, dynamic>> getServerProperties(String serverId) async {
    final res = await http.get(
      _u('/api/servers/$serverId/properties'),
      headers: _headers(json: false, auth: true),
    );
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return decoded as Map<String, dynamic>;
  }

  Future<void> updateServerProperties(String serverId, Map<String, dynamic> props) async {
    final res = await http.put(
      _u('/api/servers/$serverId/properties'),
      headers: _headers(auth: true),
      body: jsonEncode(props),
    );
    if (res.statusCode != 200) {
      final decoded = _decodeJson(res.body);
      throw ApiException(_err(decoded, res.statusCode));
    }
  }


  // ─── File management endpoints ─────────────────────────────────────────────

  Future<List<FileInfo>> listFiles(String serverId, {String path = ''}) async {
    final uri = _u('/api/servers/$serverId/files/list').replace(queryParameters: {'path': path});
    final res = await http.get(uri, headers: _headers(json: false, auth: true));
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    final items = decoded as List? ?? [];
    return items.map((e) => FileInfo.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<String> readFile(String serverId, String path) async {
    final uri = _u('/api/servers/$serverId/files/read').replace(queryParameters: {'path': path});
    final res = await http.get(uri, headers: _headers(json: false, auth: true));
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return decoded['content'] as String;
  }

  Future<void> writeFile(String serverId, String path, String content) async {
    final uri = _u('/api/servers/$serverId/files/write').replace(queryParameters: {'path': path});
    final res = await http.put(uri, headers: _headers(auth: true), body: jsonEncode({'content': content}));
    if (res.statusCode != 200) {
      final decoded = _decodeJson(res.body);
      throw ApiException(_err(decoded, res.statusCode));
    }
  }

  Future<void> deleteFileOrFolder(String serverId, String path) async {
    final uri = _u('/api/servers/$serverId/files/delete').replace(queryParameters: {'path': path});
    final res = await http.delete(uri, headers: _headers(json: false, auth: true));
    if (res.statusCode != 200) {
      final decoded = _decodeJson(res.body);
      throw ApiException(_err(decoded, res.statusCode));
    }
  }


  Future<http.Response> downloadFileBytes(String serverId, String path) async {
    final uri = _u('/api/servers/$serverId/files/download').replace(queryParameters: {'path': path});
    final res = await http.get(uri, headers: _headers(json: false, auth: true));
    if (res.statusCode != 200) {
      final decoded = _decodeJson(res.body);
      throw ApiException(_err(decoded, res.statusCode));
    }
    return res;
  }

  Future<Map<String, dynamic>> uploadFile(String serverId, String path, List<int> bytes, String filename) async {
    final uri = _u('/api/servers/$serverId/files/upload').replace(queryParameters: {'path': path});
    final req = http.MultipartRequest('POST', uri);
    if (accessToken != null && accessToken!.isNotEmpty) {
      req.headers['Authorization'] = 'Bearer $accessToken';
    }
    req.files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));
    
    final streamedRes = await req.send();
    final res = await http.Response.fromStream(streamedRes);
    final decoded = _decodeJson(res.body);
    if (res.statusCode != 200) throw ApiException(_err(decoded, res.statusCode));
    return decoded as Map<String, dynamic>;
  }
}

class FileInfo {
  final String name;
  final String path;
  final bool isDir;
  final int? size;
  final double lastModified;

  FileInfo({
    required this.name,
    required this.path,
    required this.isDir,
    this.size,
    required this.lastModified,
  });

  factory FileInfo.fromJson(Map<String, dynamic> json) {
    return FileInfo(
      name: json['name'] as String,
      path: json['path'] as String,
      isDir: json['is_dir'] as bool,
      size: json['size'] as int?,
      lastModified: (json['last_modified'] as num).toDouble(),
    );
  }
}
