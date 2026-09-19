import 'package:flutter/material.dart';
import '../../api/erex_api.dart';
import '../../state/app_state.dart';
import '../widgets/glass_card.dart';

class _AppTemplate {
  final String label;
  final String image;
  final int port;
  _AppTemplate(this.label, this.image, this.port);
}

final _templates = [
  _AppTemplate('Postgres 16', 'postgres:16', 5432),
  _AppTemplate('Redis', 'redis:latest', 6379),
  _AppTemplate('Nginx Web Server', 'nginx:latest', 80),
  _AppTemplate('Node.js 18', 'node:18', 3000),
  _AppTemplate('Custom', '', 80),
];

class CreateDeploymentScreen extends StatefulWidget {
  const CreateDeploymentScreen({super.key, required this.state});
  final AppState state;

  @override
  State<CreateDeploymentScreen> createState() => _CreateDeploymentScreenState();
}

class _CreateDeploymentScreenState extends State<CreateDeploymentScreen> {
  final _nameCtrl = TextEditingController();
  final _imageCtrl = TextEditingController();
  final _portCtrl = TextEditingController();

  _AppTemplate _selectedTemplate = _templates.first;
  int _selectedRamMb = 512;
  bool _isLoading = false;

  @override
  void initState() {
    super.initState();
    _applyTemplate();
  }

  void _applyTemplate() {
    if (_selectedTemplate.label != 'Custom') {
      _imageCtrl.text = _selectedTemplate.image;
      _portCtrl.text = _selectedTemplate.port.toString();
    }
  }

  void _submit() async {
    final name = _nameCtrl.text.trim();
    final image = _imageCtrl.text.trim();
    final port = int.tryParse(_portCtrl.text.trim()) ?? 0;

    if (name.isEmpty || image.isEmpty || port <= 0) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Please fill all fields correctly')),
      );
      return;
    }

    setState(() => _isLoading = true);
    try {
      await widget.state.api.createDeployment(
        name: name,
        dockerImage: image,
        internalPort: port,
        ramLimitMb: _selectedRamMb,
      );
      if (mounted) {
        Navigator.pop(context, true);
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Error: $e'), backgroundColor: Colors.red),
        );
      }
    } finally {
      if (mounted) {
        setState(() => _isLoading = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent, // Assumes parent has the background
      appBar: AppBar(
        title: const Text('Deploy App'),
        backgroundColor: Colors.transparent,
        elevation: 0,
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator(color: Color(0xFF06B6D4)))
          : SingleChildScrollView(
              padding: const EdgeInsets.all(16.0),
              child: Column(
                children: [
                  GlassCard(
                    child: Padding(
                      padding: const EdgeInsets.all(16.0),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('Configuration', style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.bold)),
                          const SizedBox(height: 16),
                          _buildTextField('App Name', _nameCtrl, 'e.g., My Database'),
                          const SizedBox(height: 16),
                          const Text('Template', style: TextStyle(color: Colors.white70, fontSize: 12)),
                          const SizedBox(height: 8),
                          DropdownButtonFormField<_AppTemplate>(
                            value: _selectedTemplate,
                            dropdownColor: const Color(0xFF161622),
                            decoration: _inputDeco(),
                            items: _templates.map((t) {
                              return DropdownMenuItem(
                                value: t,
                                child: Text(t.label, style: const TextStyle(color: Colors.white)),
                              );
                            }).toList(),
                            onChanged: (v) {
                              if (v != null) {
                                setState(() {
                                  _selectedTemplate = v;
                                  _applyTemplate();
                                });
                              }
                            },
                          ),
                          const SizedBox(height: 16),
                          _buildTextField('Docker Image', _imageCtrl, 'e.g., postgres:16', enabled: _selectedTemplate.label == 'Custom'),
                          const SizedBox(height: 16),
                          _buildTextField('Internal Port', _portCtrl, 'e.g., 5432', keyboardType: TextInputType.number, enabled: _selectedTemplate.label == 'Custom'),
                          const SizedBox(height: 16),
                          const Text('RAM Limit', style: TextStyle(color: Colors.white70, fontSize: 12)),
                          const SizedBox(height: 8),
                          DropdownButtonFormField<int>(
                            value: _selectedRamMb,
                            dropdownColor: const Color(0xFF161622),
                            decoration: _inputDeco(),
                            items: [256, 512, 1024, 2048, 4096].map((mb) {
                              return DropdownMenuItem(
                                value: mb,
                                child: Text('${mb}MB', style: const TextStyle(color: Colors.white)),
                              );
                            }).toList(),
                            onChanged: (v) {
                              if (v != null) setState(() => _selectedRamMb = v);
                            },
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 24),
                  SizedBox(
                    width: double.infinity,
                    height: 56,
                    child: ElevatedButton(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF06B6D4),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                      ),
                      onPressed: _submit,
                      child: const Text(
                        'Deploy Now',
                        style: TextStyle(color: Colors.black, fontSize: 16, fontWeight: FontWeight.bold),
                      ),
                    ),
                  )
                ],
              ),
            ),
    );
  }

  Widget _buildTextField(String label, TextEditingController controller, String hint, {bool enabled = true, TextInputType? keyboardType}) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: const TextStyle(color: Colors.white70, fontSize: 12)),
        const SizedBox(height: 8),
        TextField(
          controller: controller,
          enabled: enabled,
          keyboardType: keyboardType,
          style: TextStyle(color: enabled ? Colors.white : Colors.white54),
          decoration: _inputDeco().copyWith(hintText: hint),
        ),
      ],
    );
  }

  InputDecoration _inputDeco() {
    return InputDecoration(
      filled: true,
      fillColor: Colors.black.withOpacity(0.2),
      hintStyle: const TextStyle(color: Colors.white30),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(8),
        borderSide: BorderSide(color: Colors.white.withOpacity(0.1)),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(8),
        borderSide: BorderSide(color: Colors.white.withOpacity(0.1)),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(8),
        borderSide: const BorderSide(color: Color(0xFF06B6D4)),
      ),
    );
  }
}
