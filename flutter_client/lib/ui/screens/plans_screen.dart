import 'dart:ui';
import 'package:flutter/material.dart';

import '../../state/app_state.dart';
import '../theme/tranquil_theme.dart';
import '../widgets/glass_card.dart';
import '../widgets/glowing_button.dart';
import 'package:razorpay_flutter/razorpay_flutter.dart';


class PlansScreen extends StatelessWidget {
  const PlansScreen({
    super.key,
    required this.state,
    this.serverId,
    this.onPlanUpgraded,
    this.onPlanChanged,
  });

  final AppState state;
  final String? serverId;
  final VoidCallback? onPlanUpgraded;
  final VoidCallback? onPlanChanged;

  // NEW: This will eventually call your API and open Razorpay
  Future<void> _selectPlan(BuildContext context, _Plan plan) async {
    final scaffoldMessenger = ScaffoldMessenger.of(context);
    debugPrint('PlansScreen: _selectPlan called for plan ${plan.id}, serverId=$serverId');
    
    // If no serverId is provided, we are just selecting a plan for a new server
    if (serverId == null) {
      debugPrint('PlansScreen: serverId is null, selecting plan in state');
      state.selectPlan(plan.id);
      onPlanChanged?.call();
      scaffoldMessenger.showSnackBar(SnackBar(
        backgroundColor: TranquilTheme.deepWater,
        content: Text('${plan.name} selected for new server',
            style: const TextStyle(color: Colors.white, fontFamily: 'monospace', fontSize: 12)),
      ));
      return;
    }

    scaffoldMessenger.showSnackBar(SnackBar(
      backgroundColor: TranquilTheme.deepWater,
      content: Text('Initiating payment for ${plan.name}...',
          style: const TextStyle(color: Colors.white, fontFamily: 'monospace', fontSize: 12)),
    ));

    try {
      debugPrint('PlansScreen: calling createUpgradeOrder on backend');
      // 1. Create order on backend
      final order = await state.api.createUpgradeOrder(
        serverId: serverId!,
        targetPlanId: plan.id,
      );

      final orderId = order['provider_order_id'];
      debugPrint('PlansScreen: backend order created successfully. orderId=$orderId');
      
      // 2. Initialize Razorpay
      final razorpay = Razorpay();

      razorpay.on(Razorpay.EVENT_PAYMENT_SUCCESS, (PaymentSuccessResponse response) async {
        razorpay.clear();
        try {
          // 3. Verify on backend
          await state.api.verifyPayment(
            providerOrderId: response.orderId!,
            providerPaymentId: response.paymentId!,
            signature: response.signature!,
          );
          
          if (context.mounted) {
            scaffoldMessenger.showSnackBar(SnackBar(
              backgroundColor: Colors.green.shade800,
              content: const Text('Payment successful! Server upgraded.',
                  style: TextStyle(color: Colors.white, fontFamily: 'monospace', fontSize: 12)),
            ));
            onPlanUpgraded?.call();
            Navigator.of(context).pop();
          }
        } catch (e) {
          if (context.mounted) {
            scaffoldMessenger.showSnackBar(SnackBar(
              backgroundColor: Colors.red.shade900,
              content: Text('Failed to verify payment: $e',
                  style: const TextStyle(color: Colors.white, fontFamily: 'monospace', fontSize: 12)),
            ));
          }
        }
      });

      razorpay.on(Razorpay.EVENT_PAYMENT_ERROR, (PaymentFailureResponse response) {
        razorpay.clear();
        if (context.mounted) {
          scaffoldMessenger.showSnackBar(SnackBar(
            backgroundColor: Colors.red.shade900,
            content: Text('Payment failed: ${response.message}',
                style: const TextStyle(color: Colors.white, fontFamily: 'monospace', fontSize: 12)),
          ));
        }
      });

      razorpay.on(Razorpay.EVENT_EXTERNAL_WALLET, (ExternalWalletResponse response) {
        razorpay.clear();
        if (context.mounted) {
          scaffoldMessenger.showSnackBar(SnackBar(
            backgroundColor: TranquilTheme.deepWater,
            content: Text('External wallet selected: ${response.walletName}',
                style: const TextStyle(color: Colors.white, fontFamily: 'monospace', fontSize: 12)),
          ));
        }
      });

      // 4. Open Razorpay Checkout
      var options = {
        'key': 'rzp_test_T69ehXcllvB6zI', // Hardcoded as agreed
        'amount': (double.parse(order['amount'].toString()) * 100).toInt(), // paise
        'name': 'BlockHost',
        'description': 'Server Upgrade: ${plan.name}',
        'order_id': orderId,
        'prefill': {
          'contact': '',
          'email': '',
        },
        'theme': {
          'color': '#00E5FF' // TranquilTheme.glowCyan hex
        }
      };

      razorpay.open(options);

    } catch (e) {
      if (context.mounted) {
        scaffoldMessenger.showSnackBar(SnackBar(
          backgroundColor: Colors.red.shade900,
          content: Text('Failed to start checkout: $e',
              style: const TextStyle(color: Colors.white, fontFamily: 'monospace', fontSize: 12)),
        ));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final showBackButton = Navigator.of(context).canPop();
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: Stack(
        children: [
          // Background Gradient
          Container(
            decoration: const BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [Color(0xFF0A0F1A), Color(0xFF061520), Color(0xFF021B2A)],
              ),
            ),
          ),
          
          // Blur Overlay
          ClipRect(
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
              child: Container(color: Colors.transparent),
            ),
          ),

          SafeArea(
            child: Column(
              children: [
                _TranquilHeader(
                  title: 'Upgrade Server',
                  icon: Icons.workspace_premium_rounded,
                  showBackButton: showBackButton,
                ),

                Expanded(
              child: FutureBuilder<List<dynamic>>(
                future: state.api.listPlans(),
                builder: (context, snapshot) {
                  if (snapshot.connectionState == ConnectionState.waiting) {
                    return const Center(
                      child: CircularProgressIndicator(color: TranquilTheme.glowCyan, strokeWidth: 1.5),
                    );
                  }
                  if (snapshot.hasError) {
                    return Center(
                      child: Text('Failed to load plans.\n${snapshot.error}',
                          textAlign: TextAlign.center,
                          style: const TextStyle(color: Colors.white54, fontFamily: 'monospace')),
                    );
                  }

                  final data = snapshot.data ?? [];
                  
                  final plans = data.map((p) {
                    final isUltra = p['name'].toString().toLowerCase() == 'ultra';
                    final isPro = p['name'].toString().toLowerCase() == 'pro';
                    
                    return _Plan(
                      id: p['id'],
                      name: p['name'],
                      price: '₹${p['price']}',
                      period: '/mo',
                      specs: '${p['cpu_limit']}% CPU  •  ${p['ram_mb']} MB RAM',
                      limit: '${p['player_limit']} Players  •  ${p['storage_mb']} MB Storage',
                      accent: isUltra 
                          ? const Color(0xFFFFD54F) 
                          : isPro ? const Color(0xFF80D8FF) : TranquilTheme.glowCyan,
                      lily: isUltra || isPro,
                      leaf: !isPro,
                      badge: isUltra ? 'BEST VALUE' : null,
                    );
                  }).toList();

                  if (plans.isEmpty) {
                    return const Center(
                      child: Text('No plans available.',
                          style: TextStyle(color: Colors.white54, fontFamily: 'monospace')),
                    );
                  }

                  return SingleChildScrollView(
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 100),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        GlassCard(
                          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                          child: Row(children: [
                            Icon(Icons.info_outline_rounded, color: TranquilTheme.glowCyan, size: 16),
                            const SizedBox(width: 10),
                            const Expanded(
                              child: Text(
                                'Select a plan to unlock this server. Subscription applies to this server only.',
                                style: TextStyle(color: Colors.white70, fontSize: 12, fontFamily: 'monospace'),
                              ),
                            ),
                          ]),
                        ),
                        const SizedBox(height: 16),
                        ...plans.map((plan) => Padding(
                              padding: const EdgeInsets.only(bottom: 14),
                              child: _PlanCard(
                                plan: plan,
                                selected: (serverId == null) ? (state.selectedPlan == plan.id) : false, 
                                onTap: () => _selectPlan(context, plan),
                              ),
                            )),
                      ],
                    ),
                  );
                },
              ),
            ),
          ],
        ),
      ),
     ],
    ),
   );
  }
}

// ... (Keep _Plan, _PlanCard, _PlanRow, and _TranquilHeader exactly the same as you had them)
class _Plan {
  const _Plan({
    required this.id,
    required this.name,
    required this.price,
    required this.period,
    required this.specs,
    required this.limit,
    required this.accent,
    required this.lily,
    required this.leaf,
    this.badge,
  });
  final String id, name, price, period, specs, limit;
  final Color accent;
  final bool lily;
  final bool leaf;
  final String? badge;
}

class _PlanCard extends StatelessWidget {
  const _PlanCard({required this.plan, required this.selected, required this.onTap});
  final _Plan plan;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(12),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: TranquilTheme.blurSigma, sigmaY: TranquilTheme.blurSigma),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 200),
            padding: const EdgeInsets.all(18),
            decoration: BoxDecoration(
              color: selected
                  ? plan.accent.withOpacity(0.12)
                  : TranquilTheme.deepWater.withOpacity(0.4),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: selected ? plan.accent : TranquilTheme.glowCyan.withOpacity(0.25),
                width: selected ? 1.5 : 1,
              ),
            ),
            child: Stack(
              children: [
                // ── Decorative nature assets in background ────────────────
                if (plan.leaf)
                  Positioned(
                    right: -20, bottom: -20,
                    child: Opacity(
                      opacity: 0.12,
                      child: Image.asset('assets/floating_leaves.png', width: 140),
                    ),
                  ),
                if (plan.lily)
                  Positioned(
                    right: plan.leaf ? 60 : -10, bottom: -10,
                    child: Opacity(
                      opacity: 0.14,
                      child: Image.asset('assets/water_lily.png', width: 90),
                    ),
                  ),

                // ── Content ───────────────────────────────────────────────
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                        decoration: BoxDecoration(
                          color: plan.accent.withOpacity(.15),
                          border: Border.all(color: plan.accent.withOpacity(.4)),
                          borderRadius: BorderRadius.circular(20),
                        ),
                        child: Text(plan.name.toUpperCase(),
                            style: TextStyle(
                                color: plan.accent,
                                fontSize: 11,
                                fontFamily: 'monospace',
                                fontWeight: FontWeight.bold)),
                      ),
                      if (plan.badge != null) ...[
                        const SizedBox(width: 8),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                          decoration: BoxDecoration(
                            color: plan.accent.withOpacity(.2),
                            borderRadius: BorderRadius.circular(20),
                          ),
                          child: Text(plan.badge!,
                              style: TextStyle(
                                  color: plan.accent,
                                  fontSize: 9,
                                  fontFamily: 'monospace',
                                  letterSpacing: 1)),
                        ),
                      ],
                      const Spacer(),
                      if (selected)
                        Icon(Icons.check_circle_rounded, color: plan.accent, size: 18),
                    ]),

                    const SizedBox(height: 14),
                    Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
                      Text(plan.price,
                          style: TextStyle(
                              color: plan.accent,
                              fontSize: 30,
                              fontWeight: FontWeight.w900,
                              fontFamily: 'monospace')),
                      Padding(
                        padding: const EdgeInsets.only(bottom: 4, left: 2),
                        child: Text(plan.period,
                            style: const TextStyle(
                                color: Colors.white38, fontSize: 12, fontFamily: 'monospace')),
                      ),
                    ]),

                    const SizedBox(height: 12),
                    Divider(color: TranquilTheme.glowCyan.withOpacity(0.15), height: 1),
                    const SizedBox(height: 12),

                    _PlanRow(Icons.memory_rounded, plan.specs),
                    const SizedBox(height: 6),
                    _PlanRow(Icons.people_rounded, plan.limit),

                    const SizedBox(height: 16),

                    GlowingButton(
                      label: selected ? 'Selected ✓' : 'Choose ${plan.name}',
                      onTap: selected ? null : onTap,
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _PlanRow extends StatelessWidget {
  const _PlanRow(this.icon, this.text);
  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) => Row(children: [
        Icon(icon, color: TranquilTheme.textMuted, size: 14),
        const SizedBox(width: 8),
        Text(text,
            style: const TextStyle(color: Colors.white70, fontSize: 12, fontFamily: 'monospace')),
      ]);
}

// ── Shared tranquil header ─────────────────────────────────────────────────────
class _TranquilHeader extends StatelessWidget {
  const _TranquilHeader({required this.title, required this.icon, this.showBackButton = false});
  final String title;
  final IconData icon;
  final bool showBackButton;

  @override
  Widget build(BuildContext context) {
    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
        child: Container(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 14),
          decoration: BoxDecoration(
            color: TranquilTheme.deepWater.withOpacity(0.4),
            border: Border(
              bottom: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.2)),
            ),
          ),
          child: Row(
            children: [
              if (showBackButton) ...[
                GestureDetector(
                  onTap: () => Navigator.maybePop(context),
                  child: Container(
                    width: 34, height: 34,
                    decoration: BoxDecoration(
                      color: TranquilTheme.glowCyan.withOpacity(0.1),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: TranquilTheme.glowCyan.withOpacity(0.3)),
                    ),
                    child: const Icon(Icons.arrow_back_ios_new_rounded, size: 15, color: TranquilTheme.glowCyan),
                  ),
                ),
                const SizedBox(width: 12),
              ],
              Icon(icon, color: TranquilTheme.glowCyan, size: 20),
              const SizedBox(width: 10),
              Text(title,
                  style: const TextStyle(
                      color: Colors.white,
                      fontSize: 18,
                      fontWeight: FontWeight.bold)),
              const Spacer(),
              Text('EREX',
                  style: TextStyle(
                      color: TranquilTheme.glowCyan,
                      fontSize: 13,
                      fontFamily: 'monospace',
                      letterSpacing: 3)),
            ],
          ),
        ),
      ),
    );
  }
}