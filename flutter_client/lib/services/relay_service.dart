import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;

/// Erex Relay Service Client
///
/// Provides a Dart interface for the Erex relay system. Handles:
/// - Server registration and lookup via join codes
/// - Automatic heartbeat management
/// - HTTP communication with the Python relay backend
/// - Error handling and timeout management
///
/// Example usage:
/// ```dart
/// final relay = RelayService('user123', 'auth-token-123');
/// final result = await relay.registerServer('192.168.1.100', 19132);
/// print('Join code: ${relay.joinCode}');
/// ```
class RelayService {
  /// The relay backend URL (e.g., "http://localhost:8000")
  static const String defaultRelayUrl = String.fromEnvironment(
    'EREX_API_BASE_URL',
    defaultValue: 'http://localhost:8000',
  );

  /// Request timeout duration
  static const Duration requestTimeout = Duration(seconds: 10);

  /// Heartbeat interval (send every 5 minutes)
  static const Duration heartbeatInterval = Duration(minutes: 5);

  /// User identifier (device UUID or username)
  final String userId;

  /// Authentication token for this device
  final String authToken;

  /// Relay server URL (can be overridden for testing)
  final String relayUrl;

  /// Current join code (null until server is registered)
  String? _joinCode;

  /// Device IP address
  String? _deviceIp;

  /// Server port
  int? _serverPort;

  /// Whether a server is currently online
  bool _isServerOnline = false;

  /// HTTP client instance
  final http.Client _httpClient = http.Client();

  /// Timer for periodic heartbeats
  Timer? _heartbeatTimer;

  /// Private constructor
  RelayService({
    required this.userId,
    required this.authToken,
    this.relayUrl = defaultRelayUrl,
  }) {
    _log('✅', 'RelayService initialized | userId=$userId');
  }

  // =========================================================================
  // Public Properties
  // =========================================================================

  /// Get the current join code (null if not registered)
  String? get joinCode => _joinCode;

  /// Check if a server is currently online
  bool get isServerOnline => _isServerOnline;

  /// Get the device IP (null if not registered)
  String? get deviceIp => _deviceIp;

  /// Get the server port (null if not registered)
  int? get serverPort => _serverPort;

  // =========================================================================
  // Public Methods - Server Management
  // =========================================================================

  /// Register a Minecraft server with the relay system.
  ///
  /// This method:
  /// 1. Sends device information to the relay
  /// 2. Receives a join code and relay connection details
  /// 3. Starts automatic heartbeat management
  ///
  /// Parameters:
  /// - [deviceIp]: Device's local or public IP (e.g., "192.168.1.100")
  /// - [serverPort]: Minecraft server port (default 19132)
  /// - [playerCount]: Current player count (default 0)
  ///
  /// Returns a map with:
  /// - "success": bool
  /// - "join_code": String (e.g., "A1B2C3")
  /// - "relay_url": String (public relay URL)
  /// - "relay_port": int (UDP port for game traffic)
  /// - "expires_at": String (ISO 8601 timestamp)
  /// - "message": String (human-readable status)
  ///
  /// Throws [Exception] on network errors or invalid response
  ///
  /// Example:
  /// ```dart
  /// final result = await relay.registerServer('192.168.1.100', 19132);
  /// print('Join code: ${result["join_code"]}');
  /// ```
  Future<Map<String, dynamic>> registerServer(
    String deviceIp,
    int serverPort, {
    int playerCount = 0,
  }) async {
    try {
      _log('🔍', 'Registering server | ip=$deviceIp | port=$serverPort');

      _deviceIp = deviceIp;
      _serverPort = serverPort;

      final url = Uri.parse('$relayUrl/api/register');
      final body = {
        'device_id': userId,
        'device_ip': deviceIp,
        'server_port': serverPort,
        'auth_token': authToken,
        'player_count': playerCount,
      };

      final response = await _httpClient
          .post(
            url,
            headers: {'Content-Type': 'application/json'},
            body: _encodeJson(body),
          )
          .timeout(requestTimeout);

      _handleResponse(response, 201);
      final data = _decodeJson(response.body);

      // Store join code and mark as online
      _joinCode = data['join_code'];
      _isServerOnline = true;

      _log('✅', 'Server registered | code=$_joinCode | expires=${data["expires_at"]}');

      // Start heartbeat management
      _startHeartbeat();

      return data;
    } on TimeoutException {
      _log('❌', 'Registration timeout (10s exceeded)');
      rethrow;
    } catch (e) {
      _log('❌', 'Registration failed: $e');
      rethrow;
    }
  }

  /// Unregister the server and stop heartbeats.
  ///
  /// This method:
  /// 1. Sends unregister request to relay
  /// 2. Stops automatic heartbeat
  /// 3. Clears stored server info
  ///
  /// Throws [Exception] if not registered or network error
  ///
  /// Example:
  /// ```dart
  /// await relay.unregisterServer();
  /// print('Server unregistered');
  /// ```
  Future<void> unregisterServer() async {
    try {
      _log('🔍', 'Unregistering server | code=$_joinCode');

      _stopHeartbeat();

      final url = Uri.parse('$relayUrl/api/unregister');
      final body = {
        'device_id': userId,
        'auth_token': authToken,
      };

      final response = await _httpClient
          .post(
            url,
            headers: {'Content-Type': 'application/json'},
            body: _encodeJson(body),
          )
          .timeout(requestTimeout);

      _handleResponse(response, 200);

      _joinCode = null;
      _isServerOnline = false;
      _deviceIp = null;
      _serverPort = null;

      _log('✅', 'Server unregistered successfully');
    } on TimeoutException {
      _log('❌', 'Unregister timeout (10s exceeded)');
      rethrow;
    } catch (e) {
      _log('❌', 'Unregister failed: $e');
      rethrow;
    }
  }

  // =========================================================================
  // Public Static Methods - Client Lookup
  // =========================================================================

  /// Look up a server by join code (static method for clients).
  ///
  /// This is called by players who want to join a server using a join code.
  /// No authentication required.
  ///
  /// Parameters:
  /// - [joinCode]: 6-character code (e.g., "A1B2C3")
  /// - [relayUrl]: Optional relay URL override
  ///
  /// Returns a map with:
  /// - "success": bool
  /// - "device_ip": String
  /// - "server_port": int
  /// - "relay_url": String
  /// - "relay_port": int
  /// - "player_count": int
  /// - "message": String
  ///
  /// Throws [Exception] if code not found or network error
  ///
  /// Example:
  /// ```dart
  /// final server = await RelayService.lookupServer('A1B2C3');
  /// print('Connect to: ${server["device_ip"]}:${server["server_port"]}');
  /// ```
  static Future<Map<String, dynamic>> lookupServer(
    String joinCode, {
    String relayUrl = defaultRelayUrl,
  }) async {
    try {
      _logStatic('🔍', 'Looking up server | code=$joinCode');

      final url =
          Uri.parse('$relayUrl/api/lookup').replace(queryParameters: {'code': joinCode});
      final client = http.Client();

      final response = await client
          .get(url, headers: {'Content-Type': 'application/json'})
          .timeout(requestTimeout);

      _handleResponseStatic(response, 200);
      final data = _decodeJsonStatic(response.body);

      _logStatic('✅', 'Server found | ip=${data["device_ip"]} | port=${data["server_port"]}');

      client.close();
      return data;
    } on TimeoutException {
      _logStatic('❌', 'Lookup timeout (10s exceeded)');
      rethrow;
    } catch (e) {
      _logStatic('❌', 'Lookup failed: $e');
      rethrow;
    }
  }

  /// Get the device's public IP address.
  ///
  /// Uses an external service to determine public IP.
  /// Useful for cases where we can't determine IP automatically.
  ///
  /// Returns the public IP as a String (e.g., "203.0.113.42")
  /// Throws [Exception] if unable to determine public IP
  ///
  /// Example:
  /// ```dart
  /// final publicIp = await RelayService.getPublicIP();
  /// print('Public IP: $publicIp');
  /// ```
  static Future<String> getPublicIP() async {
    try {
      _logStatic('🔍', 'Fetching public IP address...');

      final url = Uri.parse('https://api.ipify.org?format=json');
      final client = http.Client();

      final response = await client
          .get(url, headers: {'User-Agent': 'Erex/1.0'})
          .timeout(const Duration(seconds: 10));

      if (response.statusCode == 200) {
        final data = _decodeJsonStatic(response.body);
        final ip = data['ip'] as String?;

        if (ip != null && ip.isNotEmpty) {
          _logStatic('✅', 'Public IP: $ip');
          client.close();
          return ip;
        }
      }

      throw Exception('Invalid response from IP service');
    } on TimeoutException {
      _logStatic('❌', 'Public IP lookup timeout (10s exceeded)');
      rethrow;
    } catch (e) {
      _logStatic('❌', 'Failed to get public IP: $e');
      rethrow;
    }
  }

  // =========================================================================
  // Private Methods - Heartbeat Management
  // =========================================================================

  /// Start automatic heartbeat timer.
  ///
  /// Sends a heartbeat every 5 minutes to keep the server registration alive.
  void _startHeartbeat() {
    _stopHeartbeat(); // Cancel any existing timer

    _log('💓', 'Starting heartbeat timer (every 5 minutes)');

    _heartbeatTimer = Timer.periodic(heartbeatInterval, (_) async {
      await _sendHeartbeat();
    });

    // Send first heartbeat immediately
    unawaited(_sendHeartbeat());
  }

  /// Stop the heartbeat timer.
  void _stopHeartbeat() {
    if (_heartbeatTimer != null) {
      _log('⏹️', 'Stopping heartbeat timer');
      _heartbeatTimer?.cancel();
      _heartbeatTimer = null;
    }
  }

  /// Send a single heartbeat to keep server alive.
  ///
  /// This is called automatically every 5 minutes.
  /// Failures are logged but don't throw exceptions.
  Future<void> _sendHeartbeat() async {
    if (_joinCode == null || !_isServerOnline) {
      return;
    }

    try {
      final url = Uri.parse('$relayUrl/api/heartbeat');
      final body = {
        'device_id': userId,
        'auth_token': authToken,
        'player_count': 0, // TODO: Get actual player count
      };

      final response = await _httpClient
          .post(
            url,
            headers: {'Content-Type': 'application/json'},
            body: _encodeJson(body),
          )
          .timeout(requestTimeout);

      if (response.statusCode == 200) {
        _log('💓', 'Heartbeat sent successfully');
      } else {
        _log('⚠️', 'Heartbeat failed: ${response.statusCode}');
      }
    } catch (e) {
      _log('⚠️', 'Heartbeat error (will retry): $e');
      // Don't rethrow - allow heartbeat to retry on next interval
    }
  }

  // =========================================================================
  // Private Methods - HTTP Utilities
  // =========================================================================

  /// Encode object to JSON string.
  static String _encodeJson(Object? object) {
    return jsonEncode(object);
  }

  /// Decode JSON string to Map.
  static Map<String, dynamic> _decodeJson(String json) {
    try {
      final decoded = jsonDecode(json);
      if (decoded is Map<String, dynamic>) {
        return decoded;
      } else if (decoded is Map) {
        return Map<String, dynamic>.from(decoded);
      } else {
        throw Exception('Expected JSON object, got ${decoded.runtimeType}');
      }
    } catch (e) {
      throw Exception('JSON decode error: $e');
    }
  }

  /// Decode JSON string to Map (static version).
  static Map<String, dynamic> _decodeJsonStatic(String json) {
    return _decodeJson(json);
  }

  /// Handle HTTP response and check status code.
  static void _handleResponse(http.Response response, int expectedStatus) {
    if (response.statusCode != expectedStatus) {
      final body = response.body;
      throw Exception('HTTP ${response.statusCode}: $body');
    }
  }

  /// Handle HTTP response and check status code (static version).
  static void _handleResponseStatic(http.Response response, int expectedStatus) {
    _handleResponse(response, expectedStatus);
  }

  // =========================================================================
  // Private Methods - Logging
  // =========================================================================

  /// Log a message with emoji prefix.
  void _log(String emoji, String message) {
    print('[$emoji] RelayService | $message');
  }

  /// Log a message with emoji prefix (static version).
  static void _logStatic(String emoji, String message) {
    print('[$emoji] RelayService | $message');
  }

  // =========================================================================
  // Public Methods - Cleanup
  // =========================================================================

  /// Clean up resources (close HTTP client, stop timers).
  ///
  /// Call this when the RelayService is no longer needed.
  /// Example:
  /// ```dart
  /// relay.dispose();
  /// ```
  void dispose() {
    _log('🛑', 'Disposing RelayService');
    _stopHeartbeat();
    _httpClient.close();
  }
}
