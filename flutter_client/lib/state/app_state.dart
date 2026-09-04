import 'package:shared_preferences/shared_preferences.dart';
import 'package:razorpay_flutter/razorpay_flutter.dart';
import 'package:flutter/foundation.dart';

import '../api/erex_api.dart';
import '../api/cached_api.dart';

class AppState {
  static const defaultBaseUrl = String.fromEnvironment(
    'EREX_API_BASE_URL',
    defaultValue: 'http://54.66.183.201',
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
  
  // Razorpay Instance
  Razorpay? _razorpay;
  
  // Callback to tell the UI when payment is done
  void Function(bool success)? onPaymentResult;

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

    // Initialize Razorpay
    if (!kIsWeb) {
      try {
        _razorpay = Razorpay();
        _razorpay!.on(Razorpay.EVENT_PAYMENT_SUCCESS, _handlePaymentSuccess);
        _razorpay!.on(Razorpay.EVENT_PAYMENT_ERROR, _handlePaymentError);
        _razorpay!.on(Razorpay.EVENT_EXTERNAL_WALLET, _handleExternalWallet);
      } catch (e) {
        debugPrint('Razorpay init failed: $e');
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

    _razorpay?.clear();
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

  // =========================================================================
  // Razorpay Payment Logic
  // =========================================================================

   Future<void> purchaseServerPlan({
    required String serverId,
    required String planId,
  }) async {
    try {
      final orderData = await api.createUpgradeOrder(
        serverId: serverId,
        targetPlanId: planId,
      );

      debugPrint('✅ ORDER DATA RECEIVED: $orderData');

      var options = {
        'key': 'rzp_test_T69ehXcllvB6zI', // PUT YOUR REAL KEY HERE
        'amount': (double.parse(orderData['amount'].toString()) * 100).toInt(),
        'name': 'Erex',
        'description': 'Server Plan Upgrade',
        'order_id': orderData['provider_order_id'], 
      };
      
      debugPrint('🚀 OPENING RAZORPAY...');
      if (_razorpay != null) {
        _razorpay!.open(options);
      } else {
        debugPrint('❌ Razorpay not available on this platform.');
        onPaymentResult?.call(false);
      }
      
    } catch (e, stacktrace) {
      // THIS WILL TELL YOU EXACTLY WHAT WENT WRONG
      debugPrint('❌❌❌ RAZORPAY CRASHED: $e');
      debugPrint('❌❌❌ STACKTRACE: $stacktrace');
      onPaymentResult?.call(false); 
    }
  }

  void _handlePaymentSuccess(PaymentSuccessResponse response) async {
    try {
      // 3. Verify signature with backend & activate server
      await api.verifyPayment(
        providerOrderId: response.orderId!,
        providerPaymentId: response.paymentId!,
        signature: response.signature!,
      );
      onPaymentResult?.call(true); // Tell UI it succeeded!
    } catch (e) {
      debugPrint('Payment succeeded but verification failed: $e');
      onPaymentResult?.call(false); 
    }
  }

  void _handlePaymentError(PaymentFailureResponse response) {
    debugPrint('Payment Error: ${response.code} - ${response.message}');
    onPaymentResult?.call(false);
  }

  void _handleExternalWallet(ExternalWalletResponse response) {
    debugPrint('External Wallet: ${response.walletName}');
  }
}