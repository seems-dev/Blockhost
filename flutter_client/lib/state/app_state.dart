import 'package:shared_preferences/shared_preferences.dart';
import 'package:flutter/foundation.dart';

import '../api/erex_api.dart';
import '../api/cached_api.dart';

class AppState {
  static const defaultBaseUrl = String.fromEnvironment(
    'EREX_API_BASE_URL',
    defaultValue: 'https://blockhost.sryze.cc',
  );

  static const _kAccessTokenKey = 'access_token';
  static const _kRefreshTokenKey = 'refresh_token';
  static const _kBaseUrlKey = 'base_url';
  static const _kHasSeenOnboardingKey = 'has_seen_onboarding';

  ErexApi api = CachedErexApi(baseUrl: defaultBaseUrl, accessToken: null);
  String _baseUrl = defaultBaseUrl;
  String get baseUrl => _baseUrl;
  String? accessToken;
  String? refreshToken;
  bool hasSeenOnboarding = false;
  
  // Payment checkout logic is now handled in UI via URL launch to Paddle

  Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    accessToken = prefs.getString(_kAccessTokenKey);
    refreshToken = prefs.getString(_kRefreshTokenKey);
    _baseUrl = prefs.getString(_kBaseUrlKey) ?? defaultBaseUrl;
    hasSeenOnboarding = prefs.getBool(_kHasSeenOnboardingKey) ?? false;
    api = CachedErexApi(baseUrl: baseUrl, accessToken: accessToken);

    if (refreshToken != null && refreshToken!.isNotEmpty) {
      try {
        final newAccess = await api.refreshAuthToken(refreshToken: refreshToken!);
        accessToken = newAccess;
        await prefs.setString(_kAccessTokenKey, newAccess);
        api = CachedErexApi(baseUrl: baseUrl, accessToken: accessToken);
      } on ApiException catch (e) {
        debugPrint('Refresh failed (API error): $e');
        await clearTokens();
      } catch (e) {
        debugPrint('Refresh failed (Network or other): $e');
      }
    }

  }

  Future<void> completeOnboarding() async {
    hasSeenOnboarding = true;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_kHasSeenOnboardingKey, true);
  }

  Future<void> setBaseUrl(String value) async {
    final trimmed = value.trim().replaceAll(RegExp(r'/+$'), '');
    if (trimmed.isEmpty) return;
    final normalized = trimmed.startsWith(RegExp(r'https?://')) ? trimmed : 'http://$trimmed';
    _baseUrl = normalized;
    api = CachedErexApi(baseUrl: baseUrl, accessToken: accessToken);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_kBaseUrlKey, normalized);
  }

  String selectedPlan = 'basic';

  void selectPlan(String planId) {
    selectedPlan = planId;
  }
  Future<void> setTokens({required String access, required String refresh}) async {
    accessToken = access;
    refreshToken = refresh;
    api = CachedErexApi(baseUrl: baseUrl, accessToken: accessToken);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_kAccessTokenKey, access);
    await prefs.setString(_kRefreshTokenKey, refresh);
  }

  Future<void> clearTokens() async {
    accessToken = null;
    refreshToken = null;
    if (api is CachedErexApi) (api as CachedErexApi).clearCache();
    api = CachedErexApi(baseUrl: baseUrl, accessToken: null);
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_kAccessTokenKey);
    await prefs.remove(_kRefreshTokenKey);
  }

  /// Revoke refresh token on server, then clear local session.
  Future<void> logout() async {
    final token = refreshToken;
    try {
      if (token != null && token.isNotEmpty) {
        await api.logout(refreshToken: token);
      }
    } catch (e) {
      debugPrint('Server logout failed (clearing local session anyway): $e');
    }
    await clearTokens();
  }

}