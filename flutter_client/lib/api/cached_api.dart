// lib/api/cached_api.dart
//
// Drop-in cached wrapper around ErexApi.
//
// Every READ method checks ApiCache first; if the entry is still fresh it
// returns immediately without hitting the network.  Every WRITE/mutation call
// delegates to the underlying API *and* invalidates the relevant cache keys so
// the next read gets a fresh response.
//
// To use, replace `api` in AppState with a CachedErexApi instance.
// All existing screen code that calls `widget.state.api.*` continues to work
// unchanged — the caching is completely transparent.

import '../models/backup_models.dart';
import '../models/ban_models.dart';
import '../services/api_cache.dart';
import 'erex_api.dart';

class CachedErexApi extends ErexApi {
  CachedErexApi({
    required super.baseUrl,
    required super.accessToken,
  });

  final _cache = ApiCache.instance;

  // ─── Server list ─────────────────────────────────────────────────────────

  @override
  Future<List<dynamic>> listServers() => _cache.get(
        key: ApiCache.servers(),
        ttl: ApiCache.serverList,
        fetch: super.listServers,
      );

  // ─── Per-server snapshot (detail + embedded stats) ────────────────────────

  @override
  Future<Map<String, dynamic>> getServer(String id) => _cache.get(
        key: ApiCache.server(id),
        ttl: ApiCache.serverDetail,
        fetch: () => super.getServer(id),
      );

  @override
  Future<Map<String, dynamic>> getProvisionStatus(String id) => super.getProvisionStatus(id);

  @override
  Future<Map<String, dynamic>> reprovisionServer(String id) async {
    final result = await super.reprovisionServer(id);
    _invalidateServer(id);
    return result;
  }

  // ─── Server stats ─────────────────────────────────────────────────────────

  @override
  Future<Map<String, dynamic>> getServerStats(String id) => _cache.get(
        key: ApiCache.serverStats_(id),
        ttl: ApiCache.serverStats,
        fetch: () => super.getServerStats(id),
      );

  // ─── Server config ────────────────────────────────────────────────────────

  @override
  Future<Map<String, dynamic>> getServerConfig(String id) => _cache.get(
        key: ApiCache.serverConfig_(id),
        ttl: ApiCache.serverConfig,
        fetch: () => super.getServerConfig(id),
      );

  // ─── Subscriptions ───────────────────────────────────────────────────────

  @override
  Future<Map<String, dynamic>> getServerSubscription(String id) => _cache.get(
        key: ApiCache.serverSubscription(id),
        ttl: ApiCache.subscription,
        fetch: () => super.getServerSubscription(id),
      );

  // ─── Plans (very rarely changes) ─────────────────────────────────────────

  @override
  Future<List<dynamic>> listPlans() => _cache.get(
        key: 'plans',
        ttl: ApiCache.plans,
        fetch: super.listPlans,
      );

  // ─── Versions catalog (changes only on new Bedrock release) ──────────────

  @override
  Future<Map<String, dynamic>> getBedrockVersions() => _cache.get(
        key: 'versions_catalog',
        ttl: ApiCache.versionCatalog,
        fetch: super.getBedrockVersions,
      );

  // ─── Backups ──────────────────────────────────────────────────────────────

  @override
  Future<List<Backup>> listBackups(String serverId) => _cache.get(
        key: ApiCache.serverBackups(serverId),
        ttl: ApiCache.backupList,
        fetch: () => super.listBackups(serverId),
      );

  // ─── Backup schedule ──────────────────────────────────────────────────────

  @override
  Future<BackupSchedule?> getBackupSchedule(String serverId) => _cache.get(
        key: ApiCache.serverBackupSchedule(serverId),
        ttl: ApiCache.backupSchedule,
        fetch: () => super.getBackupSchedule(serverId),
      );

  // ─── Blocklist ────────────────────────────────────────────────────────────

  @override
  Future<List<String>> getBlocklist(String id) => _cache.get(
        key: ApiCache.serverBlocklist(id),
        ttl: ApiCache.blocklist,
        fetch: () => super.getBlocklist(id),
      );

  // ═══════════════════════════════════════════════════════════════════════════
  // Mutations — always bypass cache, then invalidate stale entries
  // ═══════════════════════════════════════════════════════════════════════════

  @override
  Future<Map<String, dynamic>> createServerWithConfig({
    required String worldName,
    String tier = 'premium',
    String flavor = 'bedrock',
    String? mcVersion,
    Map<String, dynamic>? config,
  }) async {
    final result = await super.createServerWithConfig(
      worldName: worldName,
      tier: tier,
      flavor: flavor,
      mcVersion: mcVersion,
      config: config,
    );
    _cache.invalidate(ApiCache.servers());
    return result;
  }

  @override
  Future<Map<String, dynamic>> startServer(String id) async {
    final result = await super.startServer(id);
    _invalidateServer(id);
    return result;
  }

  @override
  Future<Map<String, dynamic>> stopServer(String id) async {
    final result = await super.stopServer(id);
    _invalidateServer(id);
    return result;
  }

  @override
  Future<Map<String, dynamic>> toggleServer(String id) async {
    final result = await super.toggleServer(id);
    _invalidateServer(id);
    return result;
  }

  @override
  Future<Map<String, dynamic>> updateServerConfig(
      String id, Map<String, dynamic> config) async {
    final result = await super.updateServerConfig(id, config);
    _cache.invalidate(ApiCache.serverConfig_(id));
    _cache.invalidate(ApiCache.server(id));
    return result;
  }

  @override
  Future<Map<String, dynamic>> getServerProperties(String serverId) => _cache.get(
        key: ApiCache.serverProperties(serverId),
        ttl: ApiCache.serverConfig, // Same TTL as config
        fetch: () => super.getServerProperties(serverId),
      );

  @override
  Future<void> updateServerProperties(String serverId, Map<String, dynamic> props) async {
    await super.updateServerProperties(serverId, props);
    _cache.invalidate(ApiCache.serverProperties(serverId));
    // Also invalidate config and snapshot since they might contain some mirrored properties
    _cache.invalidate(ApiCache.serverConfig_(serverId));
    _cache.invalidate(ApiCache.server(serverId));
  }

  @override
  Future<BackupJob> createBackup(String serverId,
      {String? name, String? description}) async {
    final result = await super.createBackup(serverId,
        name: name, description: description);
    _cache.invalidate(ApiCache.serverBackups(serverId));
    return result;
  }

  @override
  Future<void> deleteBackup(String serverId, String backupId,
      {bool deleteEvenIfPinned = false}) async {
    await super.deleteBackup(serverId, backupId,
        deleteEvenIfPinned: deleteEvenIfPinned);
    _cache.invalidate(ApiCache.serverBackups(serverId));
  }

  @override
  Future<BackupSchedule> upsertBackupSchedule(
    String serverId, {
    required bool enabled,
    required int intervalMinutes,
    required int retentionCount,
    int? retentionDays,
    bool skipIfServerOffline = false,
    bool deferIfJobActive = true,
  }) async {
    final result = await super.upsertBackupSchedule(
      serverId,
      enabled: enabled,
      intervalMinutes: intervalMinutes,
      retentionCount: retentionCount,
      retentionDays: retentionDays,
      skipIfServerOffline: skipIfServerOffline,
      deferIfJobActive: deferIfJobActive,
    );
    _cache.invalidate(ApiCache.serverBackupSchedule(serverId));
    return result;
  }

  @override
  Future<void> deleteBackupSchedule(String serverId) async {
    await super.deleteBackupSchedule(serverId);
    _cache.invalidate(ApiCache.serverBackupSchedule(serverId));
  }

  @override
  Future<Ban> createBan(String serverId,
      {required String xuid,
      required String playerName,
      String? reason,
      int? durationSeconds,
      DateTime? expiresAt}) async {
    final result = await super.createBan(serverId,
        xuid: xuid,
        playerName: playerName,
        reason: reason,
        durationSeconds: durationSeconds,
        expiresAt: expiresAt);
    // Ban changes affect online players list and blocklist
    _cache.invalidate(ApiCache.serverBlocklist(serverId));
    _cache.invalidate(ApiCache.serverStats_(serverId));
    return result;
  }

  @override
  Future<Ban> deleteBan(String serverId, String banId) async {
    final result = await super.deleteBan(serverId, banId);
    _cache.invalidate(ApiCache.serverBlocklist(serverId));
    return result;
  }

  @override
  Future<void> writeFile(String serverId, String path, String content) async {
    await super.writeFile(serverId, path, content);
    // File writes don't affect server-level cache, but invalidate config
    // in case server.properties was edited
    if (path.contains('server.properties')) {
      _cache.invalidate(ApiCache.serverConfig_(serverId));
    }
  }

  @override
  Future<void> deleteFileOrFolder(String serverId, String path) async {
    await super.deleteFileOrFolder(serverId, path);
    if (path.contains('server.properties')) {
      _cache.invalidate(ApiCache.serverConfig_(serverId));
    }
  }

  @override
  Future<Map<String, dynamic>> downloadVersion(String version) async {
    final result = await super.downloadVersion(version);
    _cache.invalidate('versions_catalog');
    return result;
  }

  // ─── Cache utilities exposed to UI ───────────────────────────────────────

  /// Force-refresh the server list on demand (e.g. pull-to-refresh).
  Future<List<dynamic>> refreshServers() =>
      _cache.refresh(key: ApiCache.servers(), fetch: super.listServers);

  Future<Map<String, dynamic>> refreshServer(String id) async {
    _cache.invalidate(ApiCache.serverSubscription(id)); // Also invalidate subscription so it gets refreshed
    return _cache.refresh(
      key: ApiCache.server(id),
      fetch: () => super.getServer(id),
    );
  }

  /// Clear all cache — call on logout.
  void clearCache() => _cache.clear();

  // ─── Private helpers ─────────────────────────────────────────────────────

  void _invalidateServer(String id) {
    _cache.invalidate(ApiCache.servers());
    _cache.invalidate(ApiCache.server(id));
    _cache.invalidate(ApiCache.serverStats_(id));
  }
}
