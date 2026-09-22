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
  _AppTemplate('GitHub Repository', '', 80),
  _AppTemplate('Custom Docker Image', '', 80),
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
  final _githubRepoCtrl = TextEditingController();
  final _githubBranchCtrl = TextEditingController(text: 'main');
  final _installCmdCtrl = TextEditingController();
  final _buildCmdCtrl = TextEditingController();
  final _startCmdCtrl = TextEditingController();

  _AppTemplate _selectedTemplate = _templates.first;
  String _selectedFramework = 'dockerfile';
  int _selectedRamMb = 512;
  bool _isLoading = false;

  @override
  void initState() {
    super.initState();
    _applyTemplate();
  }

  void _applyTemplate() {
    _portCtrl.text = _selectedTemplate.port.toString();
    if (_selectedTemplate.label != 'Custom Docker Image' && _selectedTemplate.label != 'GitHub Repository') {
      _imageCtrl.text = _selectedTemplate.image;
    } else if (_selectedTemplate.label == 'GitHub Repository') {
      _imageCtrl.clear();
    }
  }

  void _submit() async {
    final name = _nameCtrl.text.trim();
    final image = _imageCtrl.text.trim();
    final port = int.tryParse(_portCtrl.text.trim()) ?? 0;
    final githubRepo = _githubRepoCtrl.text.trim();
    final githubBranch = _githubBranchCtrl.text.trim();

    final isGithub = _selectedTemplate.label == 'GitHub Repository';

    if (name.isEmpty || port <= 0 || (isGithub ? githubRepo.isEmpty : image.isEmpty)) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Please fill all fields correctly')),
      );
      return;
    }

    setState(() => _isLoading = true);
    try {
      final project = await widget.state.api.getOrCreateDefaultProject();
      await widget.state.api.createDeployment(
        name: name,
        deploymentKind: isGithub ? _selectedFramework : null,
        dockerImage: isGithub ? null : image,
        internalPort: port > 0 ? port : null,
        ramLimitMb: _selectedRamMb,
        githubRepoUrl: isGithub ? githubRepo : null,
        githubBranch: isGithub ? githubBranch : null,
        installCommand: isGithub && _installCmdCtrl.text.isNotEmpty ? _installCmdCtrl.text : null,
        buildCommand: isGithub && _buildCmdCtrl.text.isNotEmpty ? _buildCmdCtrl.text : null,
        startCommand: isGithub && _startCmdCtrl.text.isNotEmpty ? _startCmdCtrl.text : null,
        projectId: project.id,
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
                          if (_selectedTemplate.label == 'GitHub Repository') ...[
                            _buildTextField('GitHub Repository URL', _githubRepoCtrl, 'e.g., https://github.com/user/repo'),
                            const SizedBox(height: 16),
                            _buildTextField('Branch', _githubBranchCtrl, 'e.g., main'),
                            const SizedBox(height: 16),
                            const Text('Framework', style: TextStyle(color: Colors.white70, fontSize: 12)),
                            const SizedBox(height: 8),
                            DropdownButtonFormField<String>(
                              value: _selectedFramework,
                              dropdownColor: const Color(0xFF161622),
                              decoration: _inputDeco(),
                              items: const [
                                DropdownMenuItem(value: 'dockerfile', child: Text('Dockerfile (Default)', style: TextStyle(color: Colors.white))),
                                DropdownMenuItem(value: 'nextjs', child: Text('Next.js', style: TextStyle(color: Colors.white))),
                                DropdownMenuItem(value: 'fastapi', child: Text('FastAPI (Python)', style: TextStyle(color: Colors.white))),
                                DropdownMenuItem(value: 'static_site', child: Text('React / Static HTML', style: TextStyle(color: Colors.white))),
                              ],
                              onChanged: (v) {
                                if (v != null) {
                                  setState(() {
                                    _selectedFramework = v;
                                    if (v == 'nextjs') {
                                      _installCmdCtrl.text = 'npm install';
                                      _buildCmdCtrl.text = 'npm run build';
                                      _startCmdCtrl.text = 'npm start';
                                      _portCtrl.text = '3000';
                                    } else if (v == 'fastapi') {
                                      _installCmdCtrl.text = 'pip install -r requirements.txt';
                                      _buildCmdCtrl.text = '';
                                      _startCmdCtrl.text = 'uvicorn main:app --host 0.0.0.0 --port \$PORT';
                                      _portCtrl.text = '8000';
                                    } else if (v == 'static_site') {
                                      _installCmdCtrl.text = 'npm install';
                                      _buildCmdCtrl.text = 'npm run build';
                                      _startCmdCtrl.text = '';
                                      _portCtrl.text = '80';
                                    } else {
                                      _installCmdCtrl.text = '';
                                      _buildCmdCtrl.text = '';
                                      _startCmdCtrl.text = '';
                                      _portCtrl.text = '';
                                    }
                                  });
                                }
                              },
                            ),
                            const SizedBox(height: 16),
                            ExpansionTile(
                              title: const Text('Advanced Build Settings', style: TextStyle(color: Colors.white, fontSize: 14)),
                              iconColor: const Color(0xFF06B6D4),
                              collapsedIconColor: Colors.white54,
                              tilePadding: EdgeInsets.zero,
                              children: [
                                  _buildTextField('Install Command (Optional)', _installCmdCtrl, 'e.g., npm install'),
                                  const SizedBox(height: 12),
                                  _buildTextField('Build Command (Optional)', _buildCmdCtrl, 'e.g., npm run build'),
                                  const SizedBox(height: 12),
                                  _buildTextField('Start Command (Optional)', _startCmdCtrl, 'e.g., npm start'),
                                  const SizedBox(height: 8),
                                ],
                              ),
                          ] else ...[
                            _buildTextField('Docker Image', _imageCtrl, 'e.g., ghcr.io/acme/app:latest', enabled: _selectedTemplate.label == 'Custom Docker Image'),
                          ],
                          if (_selectedTemplate.label == 'Custom Docker Image' || _selectedTemplate.label == 'GitHub Repository') ...[
                            const SizedBox(height: 16),
                            _buildTextField('Internal Port', _portCtrl, 'e.g., 5432', keyboardType: TextInputType.number),
                          ],
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
