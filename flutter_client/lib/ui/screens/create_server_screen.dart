import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../api/blockhost_api.dart';
import '../../models/server_creation.dart';
import '../../state/app_state.dart';
import '../theme/tranquil_theme.dart';

// ─── Design tokens ────────────────────────────────────────────────────────────────
const _bg = Colors.transparent;
const _surface = Color(0x80051923);
const _border = Color(0x4D64FFDA);
const _green = Color(0xFF64FFDA);
const _text = Color(0xFFE0E0E0);
const _textDim = Color(0xFFB0BEC5);
const _muted = Color(0xFF80CBC4);
const _mutedBright = Color(0xFFB2DFDB);
const _mono = 'monospace';

class CreateServerScreen extends StatefulWidget {
  const CreateServerScreen({
    super.key,
    required this.state,
    required this.onCreated,
  });

  final AppState state;
  final VoidCallback onCreated;

  @override
  State<CreateServerScreen> createState() => _CreateServerScreenState();
}

class _CreateServerScreenState extends State<CreateServerScreen>
    with SingleTickerProviderStateMixin {
  final _nameCtrl = TextEditingController();
  final List<ServerFlavor> _flavors = const [
    ServerFlavor(id: 'bedrock', name: 'Bedrock', type: ServerFlavorType.bedrock, icon: 'cube'),
    ServerFlavor(id: 'java_vanilla', name: 'Vanilla', type: ServerFlavorType.java, icon: 'coffee'),
    ServerFlavor(id: 'paper', name: 'Paper', type: ServerFlavorType.java, icon: 'bolt'),
    ServerFlavor(id: 'purpur', name: 'Purpur', type: ServerFlavorType.java, icon: 'star'),
    ServerFlavor(id: 'fabric', name: 'Fabric', type: ServerFlavorType.java, icon: 'build', comingSoon: true),
    ServerFlavor(id: 'forge', name: 'Forge', type: ServerFlavorType.java, icon: 'hammer', comingSoon: true),
    ServerFlavor(id: 'neoforge', name: 'NeoForge', type: ServerFlavorType.java, icon: 'hammer', comingSoon: true),
  ];

  int _currentStep = 0;
  bool _busy = false;
  bool _versionsLoading = false;
  String status = '';
  late AnimationController _fadeController;
  late Animation<double> _fadeAnimation;

  ServerFlavor? _selectedFlavor;
  String? _selectedVersion;
  List<String> availableVersions = [];
  List<String> installedVersions = [];
  String? recommendedVersion;

  @override
  void initState() {
    super.initState();
    _fadeController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 400),
    );
    _fadeAnimation = CurvedAnimation(
      parent: _fadeController,
      curve: Curves.easeInOut,
    );
    _fadeController.forward();
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _fadeController.dispose();
    super.dispose();
  }

  Future<void> _loadCatalog() async {
    if ((widget.state.accessToken ?? '').isEmpty) return;

    setState(() => _versionsLoading = true);
    try {
      final isJava = _selectedFlavor?.type == ServerFlavorType.java;
      final catalog = isJava
          ? await widget.state.api.getJavaVersionsCatalog()
          : await widget.state.api.getVersionsCatalog();
      final available = (catalog['available'] as List?)?.map((e) => e.toString()).toList() ?? [];
      final installed = (catalog['installed'] as List?)?.map((e) => e.toString()).toList() ?? [];
      final recommended = catalog['recommended']?.toString();

      if (!mounted) return;
      setState(() {
        availableVersions = available;
        installedVersions = installed;
        recommendedVersion = recommended;
        _versionsLoading = false;
        if (isJava) {
          if (!available.contains(_selectedVersion)) {
            _selectedVersion = recommended ?? (available.isNotEmpty ? available.first : null);
          }
        } else {
          _selectedVersion = null;
        }
      });
    } catch (_) {
      if (mounted) setState(() => _versionsLoading = false);
    }
  }

  Future<void> _handleContinue() async {
    if (_currentStep == 0) {
      if (_selectedFlavor == null) {
        setState(() => status = 'Please select a server software');
        return;
      }
      setState(() {
        status = '';
        _currentStep = 1;
        if (_selectedFlavor!.type == ServerFlavorType.java) {
          _loadCatalog();
        }
      });
      _animateStep();
      return;
    }

    if (_currentStep == 1) {
      if (_selectedFlavor?.type == ServerFlavorType.java) {
        if ((_selectedVersion ?? '').trim().isEmpty) {
          _selectedVersion = recommendedVersion ?? (availableVersions.isNotEmpty ? availableVersions.first : null);
        }
      }
      setState(() => status = '');
      setState(() => _currentStep = 2);
      _animateStep();
      return;
    }

    await _submit();
  }

  void _animateStep() {
    _fadeController.reset();
    _fadeController.forward();
  }

  Future<void> _submit() async {
    final name = _nameCtrl.text.trim();
    if (name.isEmpty) {
      setState(() => status = 'Please enter a world name');
      return;
    }
    if (_selectedFlavor == null) {
      setState(() => status = 'Please select a server flavor');
      return;
    }

    setState(() {
      _busy = true;
      status = '';
    });

    try {
      final config = <String, dynamic>{
        'server_name': name,
      };

      if (_selectedFlavor!.type == ServerFlavorType.bedrock) {
        final version = (_selectedVersion ?? '').trim();
        if (version.isNotEmpty) {
          config['bedrock_version'] = version;
        }
      }

      final mcVersion = _selectedFlavor!.type == ServerFlavorType.java
          ? (_selectedVersion ?? recommendedVersion ?? '').trim()
          : null;

      await widget.state.api.createServerWithConfig(
        worldName: name,
        tier: widget.state.selectedPlan,
        flavor: _selectedFlavor!.id,
        mcVersion: mcVersion?.isNotEmpty == true ? mcVersion : null,
        config: config,
      );

      if (!mounted) return;
      setState(() => status = 'Server created successfully!');
      widget.onCreated();
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => status = '❌ ${e.message}');
    } catch (e) {
      if (!mounted) return;
      setState(() => status = '❌ ${e.toString()}');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final bottomInset = MediaQuery.of(context).viewInsets.bottom;

    return Scaffold(
      backgroundColor: _bg,
      resizeToAvoidBottomInset: false,
      body: SafeArea(
        child: Column(
          children: [
            _buildHeader(),
            Expanded(
              child: SingleChildScrollView(
                physics: const BouncingScrollPhysics(),
                padding: EdgeInsets.fromLTRB(20, 20, 20, 32 + bottomInset),
                child: FadeTransition(
                  opacity: _fadeAnimation,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      _buildPlanBadge(),
                      const SizedBox(height: 20),
                      _buildStepIndicators(),
                      const SizedBox(height: 24),
                      _buildStatusMessage(),
                      _buildCurrentStepContent(),
                      const SizedBox(height: 28),
                      _buildActionButtons(),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // ─── Header ──────────────────────────────────────────────────────────────────

  Widget _buildHeader() {
    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
        child: Container(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 14),
          decoration: BoxDecoration(
            color: TranquilTheme.deepWater.withOpacity(0.6),
            border: Border(
              bottom: BorderSide(
                color: TranquilTheme.glowCyan.withOpacity(0.15),
                width: 1,
              ),
            ),
          ),
          child: Row(
            children: [
              Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    colors: [
                      TranquilTheme.glowCyan.withOpacity(0.2),
                      TranquilTheme.glowCyan.withOpacity(0.05),
                    ],
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                  ),
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(
                    color: TranquilTheme.glowCyan.withOpacity(0.2),
                  ),
                ),
                child: Icon(
                  Icons.add_rounded,
                  color: TranquilTheme.glowCyan,
                  size: 20,
                ),
              ),
              const SizedBox(width: 12),
              const Text(
                'Create New Server',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 18,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.3,
                ),
              ),
              const Spacer(),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
                decoration: BoxDecoration(
                  color: TranquilTheme.glowCyan.withOpacity(0.1),
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(
                    color: TranquilTheme.glowCyan.withOpacity(0.2),
                  ),
                ),
                child: Text(
                  'EREX',
                  style: TextStyle(
                    color: TranquilTheme.glowCyan,
                    fontSize: 11,
                    fontFamily: _mono,
                    letterSpacing: 2.5,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  // ─── Plan Badge ──────────────────────────────────────────────────────────────

  Widget _buildPlanBadge() {
    final planName = switch (widget.state.selectedPlan) {
      'pro' => 'Pro',
      'elite' => 'Elite',
      _ => 'Premium',
    };
    final specs = switch (widget.state.selectedPlan) {
      'pro' => '2 vCPU · 4 GB RAM · 25 players',
      'elite' => '4 vCPU · 8 GB RAM · 50 players',
      _ => '1 vCPU · 2 GB RAM · 10 players',
    };

    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [
            _green.withOpacity(0.08),
            _green.withOpacity(0.02),
          ],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: _green.withOpacity(0.15)),
      ),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(
              color: _green.withOpacity(0.1),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Icon(
              Icons.check_circle_outline_rounded,
              color: _green,
              size: 20,
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '$planName Plan Selected',
                  style: const TextStyle(
                    color: _text,
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  specs,
                  style: const TextStyle(
                    color: _muted,
                    fontSize: 11,
                    fontFamily: _mono,
                  ),
                ),
              ],
            ),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: BoxDecoration(
              color: _green.withOpacity(0.15),
              borderRadius: BorderRadius.circular(20),
            ),
            child: Text(
              'ACTIVE',
              style: TextStyle(
                color: _green,
                fontSize: 9,
                fontWeight: FontWeight.w700,
                letterSpacing: 1,
              ),
            ),
          ),
        ],
      ),
    );
  }

  // ─── Step Indicators ─────────────────────────────────────────────────────────

  Widget _buildStepIndicators() {
    const steps = ['Software', 'Version', 'Details'];
    const icons = [Icons.apps_rounded, Icons.numbers_rounded, Icons.edit_note_rounded];

    return Row(
      children: List.generate(3, (index) {
        final isActive = index == _currentStep;
        final isCompleted = index < _currentStep;
        final isLast = index == 2;

        return Expanded(
          child: Row(
            children: [
              Expanded(
                child: Column(
                  children: [
                    AnimatedContainer(
                      duration: const Duration(milliseconds: 300),
                      curve: Curves.easeInOut,
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        gradient: isCompleted || isActive
                            ? LinearGradient(
                                colors: [
                                  _green.withOpacity(isActive ? 0.25 : 0.15),
                                  _green.withOpacity(isActive ? 0.1 : 0.05),
                                ],
                                begin: Alignment.topLeft,
                                end: Alignment.bottomRight,
                              )
                            : null,
                        color: isCompleted || isActive ? null : _surface,
                        border: Border.all(
                          color: isCompleted || isActive
                              ? _green
                              : _border.withOpacity(0.5),
                          width: isCompleted || isActive ? 1.5 : 1,
                        ),
                        boxShadow: isActive
                            ? [
                                BoxShadow(
                                  color: _green.withOpacity(0.2),
                                  blurRadius: 16,
                                  spreadRadius: 1,
                                ),
                              ]
                            : null,
                      ),
                      child: isCompleted
                          ? Icon(Icons.check_rounded, color: _green, size: 18)
                          : Icon(
                              icons[index],
                              color: isActive ? _green : _muted.withOpacity(0.6),
                              size: 16,
                            ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      steps[index],
                      style: TextStyle(
                        color: isActive || isCompleted ? _text : _textDim.withOpacity(0.5),
                        fontSize: 11,
                        fontWeight: isActive ? FontWeight.w600 : FontWeight.w500,
                      ),
                    ),
                  ],
                ),
              ),
              if (!isLast)
                Expanded(
                  child: Padding(
                    padding: const EdgeInsets.only(bottom: 24),
                    child: Container(
                      height: 1.5,
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(1),
                        gradient: LinearGradient(
                          colors: [
                            if (index < _currentStep) _green else _border.withOpacity(0.3),
                            if (index + 1 < _currentStep)
                              _green
                            else if (index + 1 == _currentStep)
                              _green.withOpacity(0.3)
                            else
                              _border.withOpacity(0.1),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
            ],
          ),
        );
      }),
    );
  }

  // ─── Status Message ──────────────────────────────────────────────────────────

  Widget _buildStatusMessage() {
    if (status.isEmpty) return const SizedBox.shrink();

    final isError = status.contains('❌') || status.contains('Please');
    final isSuccess = status.contains('🚀') || status.contains('successfully');

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      margin: const EdgeInsets.only(bottom: 16),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [
            isError
                ? Colors.red.withOpacity(0.12)
                : isSuccess
                    ? _green.withOpacity(0.12)
                    : _surface,
            isError
                ? Colors.red.withOpacity(0.04)
                : isSuccess
                    ? _green.withOpacity(0.04)
                    : _surface,
          ],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: isError
              ? Colors.red.withOpacity(0.25)
              : isSuccess
                  ? _green.withOpacity(0.25)
                  : _border,
        ),
      ),
      child: Row(
        children: [
          Icon(
            isError
                ? Icons.error_outline_rounded
                : isSuccess
                    ? Icons.check_circle_outline_rounded
                    : Icons.info_outline_rounded,
            color: isError ? Colors.redAccent : isSuccess ? _green : _muted,
            size: 18,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              status,
              style: TextStyle(
                color: isError ? Colors.redAccent : isSuccess ? _green : _text,
                fontSize: 13,
                fontWeight: FontWeight.w500,
              ),
            ),
          ),
        ],
      ),
    );
  }

  // ─── Step Content Router ─────────────────────────────────────────────────────

  Widget _buildCurrentStepContent() {
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 300),
      switchInCurve: Curves.easeInOut,
      switchOutCurve: Curves.easeInOut,
      transitionBuilder: (child, animation) {
        final offsetAnimation = Tween<Offset>(
          begin: const Offset(0.05, 0),
          end: Offset.zero,
        ).animate(CurvedAnimation(parent: animation, curve: Curves.easeOut));
        return FadeTransition(
          opacity: animation,
          child: SlideTransition(position: offsetAnimation, child: child),
        );
      },
      child: switch (_currentStep) {
        0 => _buildFlavorGrid(key: const ValueKey('step0')),
        1 => _buildVersionStep(key: const ValueKey('step1')),
        2 => _buildDetailsStep(key: const ValueKey('step2')),
        _ => const SizedBox.shrink(key: ValueKey('empty')),
      },
    );
  }

  // ─── Step 0: Flavor Grid ─────────────────────────────────────────────────────

  Widget _buildFlavorGrid({Key? key}) {
    // Separate bedrock from java for visual grouping
    final bedrock = _flavors.where((f) => f.type == ServerFlavorType.bedrock).toList();
    final java = _flavors.where((f) => f.type == ServerFlavorType.java).toList();

    return Column(
      key: key,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Select Server Software',
          style: TextStyle(
            color: Colors.white.withOpacity(0.9),
            fontSize: 16,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          'Choose the server type for your world',
          style: TextStyle(
            color: _textDim.withOpacity(0.7),
            fontSize: 13,
          ),
        ),
        const SizedBox(height: 18),

        // Bedrock section
        _SectionLabel(label: 'Bedrock', icon: Icons.public_rounded),
        const SizedBox(height: 10),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: bedrock
              .map((f) => _FlavorCard(
                    flavor: f,
                    selected: _selectedFlavor?.id == f.id,
                    onTap: () => _selectFlavor(f),
                    compact: true,
                  ))
              .toList(),
        ),

        const SizedBox(height: 22),

        // Java section
        _SectionLabel(label: 'Java Edition', icon: Icons.coffee_rounded),
        const SizedBox(height: 10),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: java
              .map((f) => _FlavorCard(
                    flavor: f,
                    selected: _selectedFlavor?.id == f.id,
                    onTap: () => _selectFlavor(f),
                    compact: true,
                  ))
              .toList(),
        ),
      ],
    );
  }

  void _selectFlavor(ServerFlavor flavor) {
    if (flavor.comingSoon) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('${flavor.name} is coming soon!'),
          backgroundColor: const Color(0xFF1A2332),
          behavior: SnackBarBehavior.floating,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
          margin: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
        ),
      );
      return;
    }
    setState(() => _selectedFlavor = flavor);
    HapticFeedback.selectionClick();
  }

  // ─── Step 1: Version ─────────────────────────────────────────────────────────

  Widget _buildVersionStep({Key? key}) {
    return Column(
      key: key,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Select Version',
          style: TextStyle(
            color: Colors.white.withOpacity(0.9),
            fontSize: 16,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          _selectedFlavor?.type == ServerFlavorType.java
              ? 'Recommended version is pre-selected'
              : 'Leave empty to use the latest version',
          style: TextStyle(
            color: _textDim.withOpacity(0.7),
            fontSize: 13,
          ),
        ),
        const SizedBox(height: 18),
        _selectedFlavor?.type == ServerFlavorType.bedrock
            ? _buildBedrockVersionSelector()
            : _buildJavaVersionSelector(),
      ],
    );
  }

  Widget _buildBedrockVersionSelector() {
    return Column(
      children: [
        TextField(
          onChanged: (value) =>
              setState(() => _selectedVersion = value.isEmpty ? null : value),
          style: const TextStyle(color: _text, fontSize: 15),
          cursorColor: _green,
          decoration: _inputDecoration(
            label: 'Version (e.g. 1.21.44)',
            hint: 'Leave empty for latest',
            prefixIcon: Icons.tag_rounded,
          ),
        ),
        const SizedBox(height: 12),
        _infoTile(
          icon: Icons.info_outline_rounded,
          text: 'Bedrock servers are optimized for cross-platform play across consoles, mobile, and Windows 10.',
        ),
      ],
    );
  }

  Widget _buildJavaVersionSelector() {
    if (_versionsLoading) {
      return Container(
        padding: const EdgeInsets.all(40),
        alignment: Alignment.center,
        child: const SizedBox(
          width: 36,
          height: 36,
          child: CircularProgressIndicator(strokeWidth: 3, color: _green),
        ),
      );
    }

    final versionItems = <DropdownMenuItem<String>>[
      if (recommendedVersion != null && recommendedVersion!.isNotEmpty)
        DropdownMenuItem(
          value: recommendedVersion,
          child: Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: _green.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(
                  'REC',
                  style: TextStyle(
                    color: _green,
                    fontSize: 9,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.5,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Text(recommendedVersion!),
            ],
          ),
        ),
      ...availableVersions
          .where((v) => v.trim().isNotEmpty && v != recommendedVersion)
          .map((v) => DropdownMenuItem(value: v, child: Text(v))),
    ];

    final selectedValue = versionItems.any((item) => item.value == _selectedVersion)
        ? _selectedVersion
        : null;

    return DropdownButtonFormField<String>(
      value: selectedValue,
      decoration: _inputDecoration(
        label: 'Minecraft Version',
        prefixIcon: Icons.numbers_rounded,
      ),
      dropdownColor: const Color(0xFF1A1A2E),
      style: const TextStyle(color: _text, fontSize: 15),
      icon: const Icon(Icons.keyboard_arrow_down, color: _muted),
      items: versionItems,
      onChanged: (value) => setState(() => _selectedVersion = value),
    );
  }

  // ─── Step 2: Details ─────────────────────────────────────────────────────────

  Widget _buildDetailsStep({Key? key}) {
    return Column(
      key: key,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Final Details',
          style: TextStyle(
            color: Colors.white.withOpacity(0.9),
            fontSize: 16,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          'Name your world and review settings',
          style: TextStyle(
            color: _textDim.withOpacity(0.7),
            fontSize: 13,
          ),
        ),
        const SizedBox(height: 18),
        TextField(
          controller: _nameCtrl,
          style: const TextStyle(color: _text, fontSize: 15),
          cursorColor: _green,
          decoration: _inputDecoration(
            label: 'World Name',
            hint: 'e.g. Survival World',
            prefixIcon: Icons.dns_rounded,
          ),
        ),
        const SizedBox(height: 20),
        _buildSummaryCard(),
        if (_selectedFlavor?.type == ServerFlavorType.java) ...[
          const SizedBox(height: 14),
          _infoTile(
            icon: Icons.auto_awesome_rounded,
            text: 'Java runtime is managed automatically — no manual setup required.',
            accentColor: _green,
          ),
        ],
      ],
    );
  }

  Widget _buildSummaryCard() {
    final label = _selectedFlavor?.name ?? 'Not selected';
    final versionLabel =
        (_selectedVersion ?? recommendedVersion)?.isNotEmpty == true
            ? (_selectedVersion ?? recommendedVersion)!
            : 'Latest';

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [_surface, _surface.withOpacity(0.5)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: _border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: _green.withOpacity(0.1),
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Text(
                  'SUMMARY',
                  style: TextStyle(
                    color: _green,
                    fontSize: 9,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 1.5,
                  ),
                ),
              ),
              const Spacer(),
              Icon(Icons.checklist_rounded, color: _muted.withOpacity(0.6), size: 16),
            ],
          ),
          const SizedBox(height: 14),
          _summaryRow('Software', label),
          const SizedBox(height: 10),
          _summaryRow('Version', versionLabel),
          const SizedBox(height: 10),
          _summaryRow('Plan', widget.state.selectedPlan.toUpperCase()),
        ],
      ),
    );
  }

  Widget _summaryRow(String label, String value) {
    return Row(
      children: [
        SizedBox(
          width: 90,
          child: Text(
            label,
            style: const TextStyle(color: _textDim, fontSize: 12),
          ),
        ),
        Expanded(
          child: Text(
            value,
            style: const TextStyle(
              color: _text,
              fontSize: 13,
              fontWeight: FontWeight.w500,
            ),
          ),
        ),
      ],
    );
  }

  // ─── Action Buttons ──────────────────────────────────────────────────────────

  Widget _buildActionButtons() {
    return Column(
      children: [
        SizedBox(
          width: double.infinity,
          height: 50,
          child: ElevatedButton(
            onPressed: _busy ? null : _handleContinue,
            style: ElevatedButton.styleFrom(
              backgroundColor: _green,
              foregroundColor: Colors.black,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              elevation: 0,
              disabledBackgroundColor: _green.withOpacity(0.3),
            ),
            child: _busy && _currentStep == 2
                ? const SizedBox(
                    width: 22,
                    height: 22,
                    child: CircularProgressIndicator(
                      strokeWidth: 2.5,
                      color: Colors.black,
                    ),
                  )
                : Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text(
                        _currentStep < 2 ? 'Continue' : 'Create Server',
                        style: const TextStyle(
                          fontWeight: FontWeight.w700,
                          fontSize: 15,
                        ),
                      ),
                      if (_currentStep < 2) ...[
                        const SizedBox(width: 8),
                        const Icon(Icons.arrow_forward_rounded, size: 18),
                      ],
                    ],
                  ),
          ),
        ),
        if (_currentStep > 0) ...[
          const SizedBox(height: 10),
          SizedBox(
            width: double.infinity,
            height: 46,
            child: OutlinedButton(
              onPressed: () {
                setState(() => _currentStep--);
                _animateStep();
              },
              style: OutlinedButton.styleFrom(
                foregroundColor: _muted,
                side: BorderSide(color: _border, width: 1),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              ),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: const [
                  Icon(Icons.arrow_back_rounded, size: 16),
                  SizedBox(width: 6),
                  Text('Back', style: TextStyle(fontWeight: FontWeight.w500, fontSize: 14)),
                ],
              ),
            ),
          ),
        ],
      ],
    );
  }

  // ─── Shared Helpers ──────────────────────────────────────────────────────────

  InputDecoration _inputDecoration({
    required String label,
    String? hint,
    IconData? prefixIcon,
  }) {
    return InputDecoration(
      labelText: label,
      labelStyle: const TextStyle(color: _muted),
      hintText: hint,
      hintStyle: const TextStyle(color: _textDim),
      prefixIcon: prefixIcon != null
          ? Icon(prefixIcon, color: _muted, size: 20)
          : null,
      filled: true,
      fillColor: TranquilTheme.deepWater.withOpacity(0.5),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide.none,
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: TranquilTheme.glowCyan.withOpacity(0.15)),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: _green, width: 2),
      ),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
    );
  }

  Widget _infoTile({
    required IconData icon,
    required String text,
    Color? accentColor,
  }) {
    final color = accentColor ?? _muted;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: color.withOpacity(0.05),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: color.withOpacity(0.1)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: color, size: 16),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              text,
              style: TextStyle(color: color, fontSize: 12, height: 1.4),
            ),
          ),
        ],
      ),
    );
  }
}

// ─── Section Label ───────────────────────────────────────────────────────────────

class _SectionLabel extends StatelessWidget {
  const _SectionLabel({required this.label, required this.icon});

  final String label;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(icon, color: _muted.withOpacity(0.5), size: 15),
        const SizedBox(width: 6),
        Text(
          label.toUpperCase(),
          style: TextStyle(
            color: _muted.withOpacity(0.5),
            fontSize: 10,
            fontWeight: FontWeight.w700,
            letterSpacing: 1.5,
            fontFamily: _mono,
          ),
        ),
      ],
    );
  }
}

// ─── Flavor Card ─────────────────────────────────────────────────────────────────

class _FlavorCard extends StatelessWidget {
  const _FlavorCard({
    required this.flavor,
    required this.selected,
    required this.onTap,
    this.compact = false,
  });

  final ServerFlavor flavor;
  final bool selected;
  final VoidCallback onTap;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final width = compact ? 100.0 : 120.0;

    return Opacity(
      opacity: flavor.comingSoon ? 0.45 : 1.0,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeInOut,
        width: width,
        decoration: BoxDecoration(
          gradient: selected
              ? LinearGradient(
                  colors: [_green.withOpacity(0.15), _green.withOpacity(0.05)],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                )
              : null,
          color: selected ? null : _surface.withOpacity(0.4),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: selected ? _green : _border.withOpacity(0.5),
            width: selected ? 1.5 : 1,
          ),
          boxShadow: selected
              ? [
                  BoxShadow(
                    color: _green.withOpacity(0.12),
                    blurRadius: 16,
                    spreadRadius: 1,
                  ),
                ]
              : null,
        ),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            onTap: onTap,
            borderRadius: BorderRadius.circular(12),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 8),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  AnimatedContainer(
                    duration: const Duration(milliseconds: 200),
                    padding: const EdgeInsets.all(7),
                    decoration: BoxDecoration(
                      color: selected ? _green.withOpacity(0.15) : Colors.transparent,
                      shape: BoxShape.circle,
                    ),
                    child: Icon(
                      flavor.type == ServerFlavorType.java
                          ? Icons.coffee_outlined
                          : Icons.public_rounded,
                      color: selected ? _green : _muted.withOpacity(0.7),
                      size: 20,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    flavor.name,
                    style: TextStyle(
                      color: selected ? _text : _mutedBright,
                      fontSize: compact ? 12 : 13,
                      fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                    ),
                    textAlign: TextAlign.center,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  if (flavor.comingSoon) ...[
                    const SizedBox(height: 4),
                    Text(
                      'SOON',
                      style: TextStyle(
                        color: _textDim.withOpacity(0.4),
                        fontSize: 8,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 1,
                      ),
                    ),
                  ],
                  if (selected && !flavor.comingSoon) ...[
                    const SizedBox(height: 5),
                    Container(
                      width: 16,
                      height: 2,
                      decoration: BoxDecoration(
                        color: _green,
                        borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
