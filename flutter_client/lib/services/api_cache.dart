// lib/services/api_cache.dart
//
// Centralized TTL cache for BlockHost API responses.
//
// Cache policy at a glance:
//
//   Endpoint                    TTL       Reason
//   ─────────────────────────── ───────── ──────────────────────────────────────
//   listServers                 5 s       Shown on dashboard; changes on toggle
//   getServer (snapshot)        5 s       Per-server detail; polling replaces it
//   getServerStats              3 s       Shown in card stats; not live metrics
//   getServerConfig             30 s      Rarely changes; config edits invalidate
//   listPlans                   5 min     Almost never changes
//   getVersionsCatalog          10 min    Manifest rarely changes
//   listBackups                 10 s      Can change when a job completes
//   getBackupSchedule           30 s      Rarely changes
//   getBlocklist                15 s      Moderate change rate
//
// Live / never-cached (always fetches fresh):
//   - getServerLogs             (raw journalctl; use WebSocket instead)
//   - getBackupJob / getRestoreJob (job progress; caller polls itself)
//   - All mutation calls (POST / PUT / PATCH / DELETE)
//
// Cache is per-instance (no persistence across app restarts).
// Mutations that change data call invalidate() to remove stale entries.

import 'dart:async';

/// A single cached entry holding the value and the time it was stored.
class _Entry<T> {
  _Entry(this.value) : storedAt = DateTime.now();

  final T value;
  final DateTime storedAt;

  bool isExpired(Duration ttl) =>
      DateTime.now().difference(storedAt) > ttl;
}

/// Centralized in-memory TTL cache.
///
/// Usage:
/// ```dart
/// final cache = ApiCache();
/// final servers = await cache.get(
///   key: 'servers',
///   ttl: const Duration(seconds: 5),
///   fetch: () => api.listServers(),
/// );
/// ```
class ApiCache {
  ApiCache._();

  static final ApiCache instance = ApiCache._();

  final Map<String, _Entry<dynamic>> _store = {};

  /// Returns cached value if still fresh; otherwise calls [fetch], stores and
  /// returns the result.
  Future<T> get<T>({
    required String key,
    required Duration ttl,
    required Future<T> Function() fetch,
  }) async {
    final existing = _store[key];
    if (existing != null && !existing.isExpired(ttl)) {
      return existing.value as T;
    }
    final value = await fetch();
    _store[key] = _Entry(value);
    return value;
  }

  /// Force-refreshes a key regardless of TTL.
  Future<T> refresh<T>({
    required String key,
    required Future<T> Function() fetch,
  }) async {
    final value = await fetch();
    _store[key] = _Entry(value);
    return value;
  }

  /// Remove a specific key so the next read triggers a fresh fetch.
  void invalidate(String key) => _store.remove(key);

  /// Remove all keys that start with [prefix].
  void invalidatePrefix(String prefix) =>
      _store.removeWhere((k, _) => k.startsWith(prefix));

  /// Wipe the entire cache (e.g. on logout).
  void clear() => _store.clear();

  // ─── Convenience TTL constants ───────────────────────────────────────────

  static const Duration serverList     = Duration(seconds: 5);
  static const Duration serverDetail   = Duration(seconds: 5);
  static const Duration serverStats    = Duration(seconds: 3);
  static const Duration serverConfig   = Duration(seconds: 30);
  static const Duration plans          = Duration(minutes: 5);
  static const Duration versionCatalog = Duration(minutes: 10);
  static const Duration backupList     = Duration(seconds: 10);
  static const Duration backupSchedule = Duration(seconds: 30);
  static const Duration blocklist      = Duration(seconds: 15);
  static const Duration subscription   = Duration(seconds: 5);

  // ─── Key helpers ─────────────────────────────────────────────────────────

  static String servers()                      => 'servers';
  static String server(String id)              => 'server:$id';
  static String serverStats_(String id)        => 'server_stats:$id';
  static String serverConfig_(String id)       => 'server_config:$id';
  static String serverBackups(String id)       => 'backups:$id';
  static String serverBackupSchedule(String id)=> 'backup_schedule:$id';
  static String serverBlocklist(String id)     => 'blocklist:$id';
  static String serverSubscription(String id)  => 'subscription:$id';
  static String serverProperties(String id)    => 'properties:$id';
}
