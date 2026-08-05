import 'package:blockhost_client/services/relay_service.dart';

/// ServerManager handles the lifecycle of a Minecraft server through the relay.
///
/// Responsibilities:
/// - Starting/stopping local Minecraft servers
/// - Registering with the relay system
/// - Managing the join code for sharing with friends
/// - Joining other servers using join codes
///
/// Example usage:
/// ```dart
/// // Start hosting a server
/// final manager = ServerManager();
/// await manager.startServer();
/// print('Share this code: ${manager.joinCode}');
///
/// // Later: stop hosting
/// await manager.stopServer();
///
/// // Join a friend's server
/// await ServerManager.joinWithCode('ABC123');
/// ```
class ServerManager {
  /// Local Minecraft server port
  static const int minecraftServerPort = 19132;

  /// Device's user ID (should be unique per device)
  final String userId;

  /// Authentication token for relay communication
  final String authToken;

  /// Relay service instance
  late RelayService _relay;

  /// Whether a server is currently running locally
  bool _isLocalServerRunning = false;

  /// Local server process ID (if applicable)
  int? _serverPid;

  ServerManager({
    required this.userId,
    required this.authToken,
  }) {
    _relay = RelayService(
      userId: userId,
      authToken: authToken,
    );
    _log('✅', 'ServerManager initialized');
  }

  // =========================================================================
  // Public Properties
  // =========================================================================

  /// Get the current join code (null if not hosting)
  String? get joinCode => _relay.joinCode;

  /// Check if a server is currently online and registered
  bool get isServerOnline => _relay.isServerOnline;

  /// Check if local server is running
  bool get isLocalServerRunning => _isLocalServerRunning;

  /// Get relay URL for debugging
  String get relayUrl => _relay.relayUrl;

  // =========================================================================
  // Public Methods - Host Server
  // =========================================================================

  /// Start hosting a Minecraft server.
  ///
  /// This method:
  /// 1. Gets the device's public IP address
  /// 2. Starts a local Minecraft server process
  /// 3. Registers the server with the relay system
  /// 4. Returns the join code for sharing
  ///
  /// Returns the join code (e.g., "A1B2C3")
  /// Throws [Exception] if any step fails
  ///
  /// Example:
  /// ```dart
  /// try {
  ///   final code = await manager.startServer();
  ///   print('🎮 Server started!');
  ///   print('📋 Share this code: $code');
  ///   print('🌐 Relay URL: ${manager.relayUrl}');
  /// } catch (e) {
  ///   print('❌ Failed to start server: $e');
  /// }
  /// ```
  Future<String> startServer() async {
    try {
      _log('🔍', 'Starting server...');

      // Step 1: Ensure local server can start
      if (_isLocalServerRunning) {
        throw Exception('Server is already running!');
      }

      // Step 2: Get public IP address
      _log('🔍', 'Determining public IP address...');
      String publicIp;
      try {
        publicIp = await RelayService.getPublicIP();
        _log('✅', 'Public IP: $publicIp');
      } catch (e) {
        // Fallback to localhost for testing
        _log('⚠️', 'Could not determine public IP: $e');
        _log('⚠️', 'Falling back to localhost (testing only)');
        publicIp = '49.43.40.85';
      }

      // Step 3: Start local Minecraft server
      _log('🎮', 'Starting local Minecraft server on port $minecraftServerPort...');
      await _startLocalServer();
      _log('✅', 'Local server started');

      // Step 4: Register with relay
      _log('🌐', 'Registering with relay system...');
      final registerResult = await _relay.registerServer(
        publicIp,
        minecraftServerPort,
        playerCount: 0,
      );

      final joinCode = registerResult['join_code'] as String;
      final expiresAt = registerResult['expires_at'] as String;

      _log('✅', 'Server registered successfully!');
      _log('📋', 'Join code: $joinCode');
      _log('📅', 'Expires: $expiresAt');
      _log('🌐', 'Relay URL: ${registerResult["relay_url"]}');
      _log('📡', 'UDP Port: ${registerResult["relay_port"]}');

      print('');
      print('╔════════════════════════════════════════╗');
      print('║   🎮 MINECRAFT SERVER STARTED 🎮      ║');
      print('╚════════════════════════════════════════╝');
      print('');
      print('   📋 Share this code with friends:');
      print('   ➜ $joinCode');
      print('');
      print('   🌐 Relay URL: ${registerResult["relay_url"]}');
      print('   📡 Port: ${registerResult["relay_port"]}');
      print('   ✅ Expires at: $expiresAt');
      print('');

      return joinCode;
    } catch (e) {
      _log('❌', 'Failed to start server: $e');
      // Clean up if we partially started
      await _stopLocalServer();
      rethrow;
    }
  }

  /// Stop hosting the Minecraft server.
  ///
  /// This method:
  /// 1. Unregisters from the relay system
  /// 2. Stops the local Minecraft server process
  /// 3. Clears the join code
  ///
  /// Throws [Exception] if unable to stop
  ///
  /// Example:
  /// ```dart
  /// try {
  ///   await manager.stopServer();
  ///   print('✅ Server stopped');
  /// } catch (e) {
  ///   print('❌ Failed to stop: $e');
  /// }
  /// ```
  Future<void> stopServer() async {
    try {
      _log('🔍', 'Stopping server...');

      if (!_isLocalServerRunning) {
        _log('⚠️', 'Server is not running');
        return;
      }

      // Step 1: Unregister from relay
      _log('🌐', 'Unregistering from relay system...');
      await _relay.unregisterServer();
      _log('✅', 'Unregistered from relay');

      // Step 2: Stop local server
      _log('🎮', 'Stopping local Minecraft server...');
      await _stopLocalServer();
      _log('✅', 'Local server stopped');

      print('');
      print('✅ Server has been stopped and removed from relay');
      print('');
    } catch (e) {
      _log('❌', 'Error stopping server: $e');
      rethrow;
    }
  }

  // =========================================================================
  // Public Static Methods - Join Server
  // =========================================================================

  /// Join a server using a join code (static method for clients).
  ///
  /// This method:
  /// 1. Looks up the server by join code
  /// 2. Extracts connection information
  /// 3. Returns the connection details
  ///
  /// Parameters:
  /// - [joinCode]: 6-character code (e.g., "A1B2C3")
  /// - [relayUrl]: Optional relay URL (for testing)
  ///
  /// Returns a map with:
  /// - "host": String (device IP to connect to)
  /// - "port": int (server port)
  /// - "relay_host": String (relay IP for forwarding)
  /// - "relay_port": int (relay UDP port)
  /// - "success": bool
  ///
  /// Throws [Exception] if code not found or network error
  ///
  /// Example:
  /// ```dart
  /// try {
  ///   final serverInfo = await ServerManager.joinWithCode('ABC123');
  ///   print('✅ Server found!');
  ///   print('📍 Connect to: ${serverInfo["host"]}:${serverInfo["port"]}');
  ///   await connectToServer(serverInfo['host'], serverInfo['port']);
  /// } catch (e) {
  ///   print('❌ Code not found or invalid: $e');
  /// }
  /// ```
  static Future<Map<String, dynamic>> joinWithCode(
    String joinCode, {
    String relayUrl = RelayService.defaultRelayUrl,
  }) async {
    try {
      _logStatic('🔍', 'Looking up server with code: $joinCode');

      // Look up server by code
      final serverInfo = await RelayService.lookupServer(
        joinCode,
        relayUrl: relayUrl,
      );

      _logStatic('✅', 'Server found!');
      _logStatic('📍', 'Host: ${serverInfo["device_ip"]}:${serverInfo["server_port"]}');
      _logStatic('👥', 'Players: ${serverInfo["player_count"]}');

      // Format response for client connection
      final connectionInfo = {
        'success': true,
        'host': serverInfo['device_ip'],
        'port': serverInfo['server_port'],
        'relay_host': serverInfo['relay_url'],
        'relay_port': serverInfo['relay_port'],
        'player_count': serverInfo['player_count'],
        'message': 'Ready to connect!',
      };

      print('');
      print('╔════════════════════════════════════════╗');
      print('║     🎮 SERVER FOUND - READY TO JOIN 🎮 ║');
      print('╚════════════════════════════════════════╝');
      print('');
      print('   📍 Host: ${connectionInfo["host"]}');
      print('   🔌 Port: ${connectionInfo["port"]}');
      print('   👥 Players: ${connectionInfo["player_count"]}');
      print('   🌐 Relay: ${connectionInfo["relay_host"]}');
      print('');

      return connectionInfo;
    } catch (e) {
      _logStatic('❌', 'Failed to find server: $e');
      print('');
      print('❌ Server not found. Check that:');
      print('   • The join code is correct');
      print('   • The host has started their server');
      print('   • Both devices can reach the relay');
      print('');
      rethrow;
    }
  }

  // =========================================================================
  // Private Methods - Local Server Management
  // =========================================================================

  /// Start the local Minecraft server process.
  ///
  /// In production, this would:
  /// - Execute the Minecraft Bedrock server binary
  /// - Manage the server process lifecycle
  /// - Handle crashes and restarts
  ///
  /// For now, this is a placeholder implementation.
  Future<void> _startLocalServer() async {
    try {
      // TODO: Implement actual Minecraft server startup
      // This would involve:
      // - Finding the Minecraft server binary
      // - Starting it with appropriate parameters
      // - Managing the process lifecycle
      // - Handling server crashes
      //
      // Placeholder implementation:

      _log('🎮', 'Starting Minecraft Bedrock server...');
      _log('📝', 'Binary: minecraft_server');
      _log('📝', 'Port: $minecraftServerPort');
      _log('📝', 'Memory: 512MB');

      // Simulate server startup delay
      await Future.delayed(const Duration(milliseconds: 500));

      _isLocalServerRunning = true;
      _serverPid = 12345; // Placeholder PID

      _log('✅', 'Minecraft server started (PID: ${_serverPid ?? "unknown"})');
    } catch (e) {
      _log('❌', 'Failed to start local server: $e');
      rethrow;
    }
  }

  /// Stop the local Minecraft server process.
  ///
  /// In production, this would:
  /// - Send shutdown command to the server
  /// - Wait for graceful shutdown
  /// - Force kill if necessary
  /// - Cleanup resources
  Future<void> _stopLocalServer() async {
    try {
      if (!_isLocalServerRunning) {
        return;
      }

      _log('🎮', 'Stopping Minecraft server (PID: ${_serverPid ?? "unknown"})...');

      // TODO: Implement actual Minecraft server shutdown
      // This would involve:
      // - Sending SIGTERM to the process
      // - Waiting for graceful shutdown
      // - Force SIGKILL if it doesn't exit
      // - Cleanup resources

      // Simulate shutdown delay
      await Future.delayed(const Duration(milliseconds: 300));

      _isLocalServerRunning = false;
      _serverPid = null;

      _log('✅', 'Minecraft server stopped');
    } catch (e) {
      _log('❌', 'Error stopping local server: $e');
      _isLocalServerRunning = false;
    }
  }

  // =========================================================================
  // Private Methods - Logging
  // =========================================================================

  /// Log a message with emoji prefix.
  void _log(String emoji, String message) {
    print('[$emoji] ServerManager | $message');
  }

  /// Log a message with emoji prefix (static version).
  static void _logStatic(String emoji, String message) {
    print('[$emoji] ServerManager | $message');
  }

  // =========================================================================
  // Public Methods - Cleanup
  // =========================================================================

  /// Clean up resources (stop server, dispose relay service).
  ///
  /// Should be called when the ServerManager is no longer needed.
  ///
  /// Example:
  /// ```dart
  /// await manager.dispose();
  /// ```
  Future<void> dispose() async {
    _log('🛑', 'Disposing ServerManager');

    try {
      if (_relay.isServerOnline) {
        await _relay.unregisterServer();
      }
      await _stopLocalServer();
    } catch (e) {
      _log('⚠️', 'Error during cleanup: $e');
    }

    _relay.dispose();
  }
}

// ============================================================================
// Usage Examples
// ============================================================================

/// Example 1: Host a server
///
/// ```dart
/// void main() async {
///   final manager = ServerManager(
///     userId: 'device-123',
///     authToken: 'secret-token-456',
///   );
///
///   try {
///     // Start hosting
///     final joinCode = await manager.startServer();
///     print('🎮 Hosting! Share code: $joinCode');
///
///     // Keep server alive for a while
///     await Future.delayed(Duration(minutes: 10));
///
///     // Stop hosting
///     await manager.stopServer();
///     print('✅ Server stopped');
///   } catch (e) {
///     print('❌ Error: $e');
///   } finally {
///     await manager.dispose();
///   }
/// }
/// ```

/// Example 2: Join a server
///
/// ```dart
/// void main() async {
///   try {
///     // Join using code
///     final serverInfo = await ServerManager.joinWithCode('ABC123');
///
///     print('✅ Found server!');
///     print('📍 Connecting to ${serverInfo["host"]}:${serverInfo["port"]}');
///
///     // Connect to the Minecraft server
///     await connectToMinecraftServer(
///       serverInfo['host'],
///       serverInfo['port'],
///     );
///   } catch (e) {
///     print('❌ Failed to join: $e');
///   }
/// }
/// ```

/// Example 3: Full application flow
///
/// ```dart
/// class BlockHostApp {
///   late ServerManager _manager;
///
///   Future<void> startHosting(String userId, String authToken) async {
///     _manager = ServerManager(
///       userId: userId,
///       authToken: authToken,
///     );
///
///     try {
///       final code = await _manager.startServer();
///       showJoinCodeDialog(code);
///     } catch (e) {
///       showErrorDialog('Failed to start server: $e');
///     }
///   }
///
///   Future<void> stopHosting() async {
///     try {
///       await _manager.stopServer();
///       showSuccessDialog('Server stopped');
///     } finally {
///       await _manager.dispose();
///     }
///   }
///
///   static Future<void> joinServer(String joinCode) async {
///     try {
///       final info = await ServerManager.joinWithCode(joinCode);
///       // Navigate to game screen and connect
///       launchGameAndConnect(info['host'], info['port']);
///     } catch (e) {
///       showErrorDialog('Invalid code: $e');
///     }
///   }
/// }
/// ```
