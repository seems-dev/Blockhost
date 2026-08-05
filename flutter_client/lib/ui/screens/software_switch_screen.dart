import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../api/blockhost_api.dart';
import '../../models/server_creation.dart';
import '../../state/app_state.dart';

const _surface = Color(0x80051923);
const _border = Color(0x4D64FFDA);
const _green = Color(0xFF64FFDA);
const _text = Color(0xFFE0E0E0);
const _textDim = Color(0xFFB0BEC5);
const _mono = 'monospace';

class SoftwareSwitchScreen extends StatefulWidget {
  const SoftwareSwitchScreen({
    super.key,
    required this.state,
    required this.serverId,
    required this.currentFlavor,
  });

  final AppState state;
  final String serverId;
  final String currentFlavor;

  @override
  State<SoftwareSwitchScreen> createState() => _SoftwareSwitchScreenState();
}

class _SoftwareSwitchScreenState extends State<SoftwareSwitchScreen> with SingleTickerProviderStateMixin {
  final List<ServerFlavor> _flavors = const [
    ServerFlavor(id: 'bedrock', name: 'Bedrock', type: ServerFlavorType.bedrock, icon: 'cube'),
    ServerFlavor(id: 'java_vanilla', name: 'Vanilla', type: ServerFlavorType.java, icon: 'coffee'),
    ServerFlavor(id: 'paper', name: 'Paper', type: ServerFlavorType.java, icon: 'bolt'),
    ServerFlavor(id: 'purpur', name: 'Purpur', type: ServerFlavorType.java, icon: 'star'),
    ServerFlavor(id: 'fabric', name: 'Fabric', type: ServerFlavorType.java, icon: 'build', comingSoon: true),
    ServerFlavor(id: 'forge', name: 'Forge', type: ServerFlavorType.java, icon: 'hammer', comingSoon: true),
    ServerFlavor(id: 'neoforge', name: 'NeoForge', type: ServerFlavorType.java, icon: 'hammer', comingSoon: true),
  ];

  late AnimationController _fadeController;
  late Animation<double> _fadeAnimation;
  int _currentStep = 0;
  bool _busy = false;
  bool _versionsLoading = false;
  String status = '';

  ServerFlavor? _selectedFlavor;
  String? _selectedVersion;
  List<String> availableVersions = [];
  String? recommendedVersion;
  
  bool get _isCurrentlyJava {
    return _flavors.firstWhere((f) => f.id == widget.currentFlavor, orElse: () => _flavors.first).type == ServerFlavorType.java;
  }

  @override
  void initState() {
    super.initState();
    _fadeController = AnimationController(vsync: this, duration: const Duration(milliseconds: 400));
    _fadeAnimation = CurvedAnimation(parent: _fadeController, curve: Curves.easeInOut);
    _fadeController.forward();
    
    // Pre-select current flavor if possible
    try {
      _selectedFlavor = _flavors.firstWhere((f) => f.id == widget.currentFlavor);
    } catch (_) {}
  }

  @override
  void dispose() {
    _fadeController.dispose();
    super.dispose();
  }

  Future<void> _loadCatalog() async {
    setState(() => _versionsLoading = true);
    try {
      final isJava = _selectedFlavor?.type == ServerFlavorType.java;
      final catalog = isJava
          ? await widget.state.api.getJavaVersionsCatalog()
          : await widget.state.api.getVersionsCatalog();
      final available = (catalog['available'] as List?)?.map((e) => e.toString()).toList() ?? [];
      final recommended = catalog['recommended']?.toString();

      if (!mounted) return;
      setState(() {
        availableVersions = available;
        recommendedVersion = recommended;
        _versionsLoading = false;
        if (isJava) {
          if (!available.contains(_selectedVersion)) {
            _selectedVersion = recommended ?? (available.isNotEmpty ? available.first : null);
          }
        }
      });
    } catch (_) {
      if (mounted) setState(() => _versionsLoading = false);
    }
  }

  void _selectFlavor(ServerFlavor flavor) {
    if (flavor.comingSoon) return;
    // Prevent cross-platform switching as per plan
    if (_isCurrentlyJava != (flavor.type == ServerFlavorType.java)) {
      setState(() => status = 'Cross-platform switching is not supported.');
      return;
    }
    setState(() {
      _selectedFlavor = flavor;
      status = '';
    });
    HapticFeedback.selectionClick();
  }

  void _animateStep() {
    _fadeController.reset();
    _fadeController.forward();
  }

  void _handleContinue() {
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
      setState(() {
        status = '';
        _currentStep = 2;
      });
      _animateStep();
      return;
    }
  }

  Future<void> _pollBackup(String jobId) async {
    const maxAttempts = 120; // 4 minutes
    for (var i = 0; i < maxAttempts; i++) {
      if (!mounted) return;
      final job = await widget.state.api.getBackupJob(widget.serverId, jobId);
      if (job.isCompleted) return;
      if (job.isFailed) throw ApiException('Backup failed: ${job.errorMessage ?? "unknown error"}');
      await Future.delayed(const Duration(seconds: 2));
    }
    throw ApiException('Backup timed out.');
  }

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      status = 'Creating backup...';
    });

    try {
      // 1. Create Backup
      final backupJob = await widget.state.api.createBackup(
        widget.serverId,
        name: 'Pre-switch backup - ${_selectedFlavor!.name}',
        description: 'Automatic backup before switching software to ${_selectedFlavor!.name} ${_selectedVersion ?? ""}',
      );
      
      setState(() => status = 'Waiting for backup to complete...');
      await _pollBackup(backupJob.jobId);
      
      // 2. Switch software
      setState(() => status = 'Switching software...');
      await widget.state.api.switchSoftware(
        widget.serverId,
        _selectedFlavor!.id,
        (_selectedVersion ?? '').trim(),
      );
      
      if (!mounted) return;
      setState(() => status = '🚀 Software switched successfully!');
      await Future.delayed(const Duration(seconds: 2));
      if (!mounted) return;
      Navigator.of(context).pop();
    } on ApiException catch (e) {
      if (mounted) setState(() => status = '❌ ${e.message}');
    } catch (e) {
      if (mounted) setState(() => status = '❌ Error switching software');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _buildFlavorCard(ServerFlavor flavor, bool isCompatible) {
    final isSelected = _selectedFlavor?.id == flavor.id;
    return GestureDetector(
      onTap: isCompatible && !_busy ? () => _selectFlavor(flavor) : null,
      child: Container(
        width: 140,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: isSelected ? _green.withOpacity(0.15) : _surface,
          border: Border.all(
            color: isSelected ? _green : _border.withOpacity(0.3),
            width: isSelected ? 1.5 : 1,
          ),
          borderRadius: BorderRadius.circular(12),
          boxShadow: isSelected
              ? [BoxShadow(color: _green.withOpacity(0.1), blurRadius: 10)]
              : null,
        ),
        child: Column(
          children: [
            Icon(
              flavor.type == ServerFlavorType.java ? Icons.coffee_rounded : Icons.public_rounded,
              color: isCompatible ? (isSelected ? _green : _text) : _textDim.withOpacity(0.3),
              size: 28,
            ),
            const SizedBox(height: 8),
            Text(
              flavor.name,
              style: TextStyle(
                color: isCompatible ? (isSelected ? _green : _text) : _textDim.withOpacity(0.3),
                fontWeight: FontWeight.bold,
              ),
            ),
            if (flavor.comingSoon)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('Coming Soon', style: TextStyle(color: _textDim, fontSize: 10)),
              )
            else if (!isCompatible)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('Incompatible', style: TextStyle(color: Colors.redAccent, fontSize: 10)),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildStep0() {
    final javaFlavors = _flavors.where((f) => f.type == ServerFlavorType.java).toList();
    final bedrockFlavors = _flavors.where((f) => f.type == ServerFlavorType.bedrock).toList();
    
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('Select New Software', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
        const SizedBox(height: 16),
        if (_isCurrentlyJava) ...[
          const Text('Java Edition Flavors', style: TextStyle(color: _textDim)),
          const SizedBox(height: 8),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: javaFlavors.map((f) => _buildFlavorCard(f, true)).toList(),
          ),
        ] else ...[
          const Text('Bedrock Edition Flavors', style: TextStyle(color: _textDim)),
          const SizedBox(height: 8),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: bedrockFlavors.map((f) => _buildFlavorCard(f, true)).toList(),
          ),
        ],
      ],
    );
  }

  Widget _buildStep1() {
    if (_selectedFlavor?.type == ServerFlavorType.bedrock) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Select Version', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
          const SizedBox(height: 16),
          TextField(
            onChanged: (val) => setState(() => _selectedVersion = val),
            style: const TextStyle(color: _text),
            decoration: InputDecoration(
              labelText: 'Version (e.g. 1.20.10)',
              hintText: 'Leave empty for latest',
              filled: true,
              fillColor: _surface,
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
            ),
          ),
        ],
      );
    }
    
    if (_versionsLoading) {
      return const Center(child: CircularProgressIndicator(color: _green));
    }
    
    final items = availableVersions.map((v) => DropdownMenuItem(value: v, child: Text(v))).toList();
    if (recommendedVersion != null && !availableVersions.contains(recommendedVersion)) {
      items.insert(0, DropdownMenuItem(value: recommendedVersion, child: Text('$recommendedVersion (Recommended)')));
    }
    
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('Select Version', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
        const SizedBox(height: 16),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          decoration: BoxDecoration(
            color: _surface,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: _border),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              value: _selectedVersion,
              isExpanded: true,
              dropdownColor: const Color(0xFF0D1B2A),
              style: const TextStyle(color: _text),
              items: items,
              onChanged: (val) => setState(() => _selectedVersion = val),
            ),
          ),
        ),
      ],
    );
  }
  
  Widget _buildStep2() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: _surface,
        border: Border.all(color: Colors.orangeAccent.withOpacity(0.5)),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        children: [
          const Icon(Icons.warning_amber_rounded, color: Colors.orangeAccent, size: 32),
          const SizedBox(height: 12),
          const Text(
            'Compatibility Warning',
            style: TextStyle(color: Colors.orangeAccent, fontWeight: FontWeight.bold, fontSize: 16),
          ),
          const SizedBox(height: 8),
          Text(
            'Switching from ${widget.currentFlavor} to ${_selectedFlavor?.name} could cause world corruption if the versions are not compatible. A backup will be taken automatically before the switch.',
            textAlign: TextAlign.center,
            style: const TextStyle(color: _textDim, height: 1.4),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black.withOpacity(0.8),
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: const Text('Switch Software', style: TextStyle(fontFamily: _mono)),
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (status.isNotEmpty)
                Container(
                  padding: const EdgeInsets.all(12),
                  margin: const EdgeInsets.only(bottom: 20),
                  decoration: BoxDecoration(
                    color: _surface,
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: status.contains('❌') ? Colors.redAccent : _green),
                  ),
                  child: Text(status, style: TextStyle(color: status.contains('❌') ? Colors.redAccent : _green)),
                ),
                
              Expanded(
                child: FadeTransition(
                  opacity: _fadeAnimation,
                  child: SingleChildScrollView(
                    child: switch (_currentStep) {
                      0 => _buildStep0(),
                      1 => _buildStep1(),
                      2 => _buildStep2(),
                      _ => const SizedBox(),
                    },
                  ),
                ),
              ),
              
              const SizedBox(height: 20),
              Row(
                children: [
                  if (_currentStep > 0)
                    Expanded(
                      child: OutlinedButton(
                        onPressed: _busy ? null : () {
                          setState(() => _currentStep--);
                          _animateStep();
                        },
                        style: OutlinedButton.styleFrom(
                          padding: const EdgeInsets.symmetric(vertical: 16),
                          side: const BorderSide(color: _border),
                        ),
                        child: const Text('BACK', style: TextStyle(color: _text)),
                      ),
                    ),
                  if (_currentStep > 0) const SizedBox(width: 12),
                  Expanded(
                    flex: 2,
                    child: ElevatedButton(
                      onPressed: _busy ? null : (_currentStep == 2 ? _submit : _handleContinue),
                      style: ElevatedButton.styleFrom(
                        padding: const EdgeInsets.symmetric(vertical: 16),
                        backgroundColor: _green,
                        foregroundColor: Colors.black,
                      ),
                      child: _busy
                          ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(color: Colors.black, strokeWidth: 2))
                          : Text(_currentStep == 2 ? 'BACKUP & SWITCH' : 'CONTINUE', style: const TextStyle(fontWeight: FontWeight.bold)),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
