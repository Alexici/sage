import 'package:flutter/material.dart';

import '../net/sage_client.dart';
import '../state/sage_controller.dart';
import '../theme/sage_theme.dart';
import 'widgets/frosted_box.dart';

class SettingsScreen extends StatefulWidget {
  final SageController controller;
  final String? initialAuthError;
  final VoidCallback onClose;

  const SettingsScreen({
    super.key,
    required this.controller,
    this.initialAuthError,
    required this.onClose,
  });

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final TextEditingController _addrCtrl;
  late final TextEditingController _tokenCtrl;
  bool _showToken = false;
  bool _testing = false;
  ConnectionTestResult? _testResult;

  @override
  void initState() {
    super.initState();
    _addrCtrl = TextEditingController(text: widget.controller.serverAddress);
    _tokenCtrl = TextEditingController(text: widget.controller.deviceToken);
    if (widget.initialAuthError != null) {
      _testResult = ConnectionTestResult(
        ok: false,
        step: 'ws',
        message: widget.initialAuthError!,
      );
    }
  }

  @override
  void dispose() {
    _addrCtrl.dispose();
    _tokenCtrl.dispose();
    super.dispose();
  }

  Future<void> _runTest() async {
    setState(() {
      _testing = true;
      _testResult = null;
    });
    await widget.controller.updateSettings(
      address: _addrCtrl.text,
      token: _tokenCtrl.text,
    );
    final res = await SageNetClient.testConnection(
      address: _addrCtrl.text,
      token: _tokenCtrl.text,
    );
    if (!mounted) return;
    setState(() {
      _testing = false;
      _testResult = res;
    });
  }

  Future<void> _saveAndClose() async {
    await widget.controller.updateSettings(
      address: _addrCtrl.text,
      token: _tokenCtrl.text,
      reconnectNow: true,
    );
    widget.onClose();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final t = SageTheme(textScale: c.textScale);

    return Container(
      color: SageColors.bg,
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 560),
          child: ListView(
            padding: const EdgeInsets.all(28),
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text('Settings', style: t.title),
                  IconButton(
                    icon: const Icon(Icons.close, size: 18),
                    color: SageColors.textMuted,
                    tooltip: 'Close Settings',
                    onPressed: _saveAndClose,
                  ),
                ],
              ),
              const SizedBox(height: 20),
              Text('Server address', style: t.label),
              const SizedBox(height: 8),
              _buildTextField(
                controller: _addrCtrl,
                theme: t,
                hint: '127.0.0.1:8765',
              ),
              const SizedBox(height: 16),
              Text('Device token', style: t.label),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: _buildTextField(
                      controller: _tokenCtrl,
                      theme: t,
                      hint: 'Paste token from sage devices add',
                      obscure: !_showToken,
                    ),
                  ),
                  const SizedBox(width: 8),
                  _OutlinedButton(
                    label: _showToken ? 'Hide' : 'Show',
                    theme: t,
                    onTap: () => setState(() => _showToken = !_showToken),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  _OutlinedButton(
                    label: _testing ? 'Testing...' : 'Test connection',
                    theme: t,
                    onTap: _testing ? null : _runTest,
                  ),
                  const SizedBox(width: 12),
                  _AccentButton(
                    label: 'Save & Connect',
                    theme: t,
                    onTap: _saveAndClose,
                  ),
                ],
              ),
              if (_testResult != null) ...[
                const SizedBox(height: 12),
                FrostedBox(
                  padding: const EdgeInsets.all(12),
                  borderRadius: SageTheme.chipRadius,
                  borderColor: _testResult!.ok
                      ? SageColors.ok
                      : SageColors.danger,
                  child: Text(
                    _testResult!.message,
                    style: t.body.copyWith(
                      color: _testResult!.ok
                          ? SageColors.ok
                          : SageColors.danger,
                    ),
                  ),
                ),
              ],
              const SizedBox(height: 24),
              const Divider(color: SageColors.border, height: 1),
              const SizedBox(height: 20),
              _ToggleRow(
                label: 'Show timings & telemetry',
                value: c.showTimings,
                theme: t,
                onChanged: (v) => c.updateSettings(showTimings: v),
              ),
              const SizedBox(height: 12),
              _ToggleRow(
                label: 'Show session guard',
                value: c.showSessionGuard,
                theme: t,
                onChanged: (v) => c.updateSettings(showSessionGuard: v),
              ),
              const SizedBox(height: 12),
              _ToggleRow(
                label: 'Show tool details',
                value: c.showToolDetails,
                theme: t,
                onChanged: (v) => c.updateSettings(showToolDetails: v),
              ),
              const SizedBox(height: 20),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text('Text size', style: t.body),
                  Text('${c.textSizePercent}%', style: t.monoLabel),
                ],
              ),
              SliderTheme(
                data: SliderTheme.of(context).copyWith(
                  activeTrackColor: SageColors.accent,
                  inactiveTrackColor: SageColors.surface2,
                  thumbColor: SageColors.accent,
                ),
                child: Slider(
                  value: c.textSizePercent.toDouble(),
                  min: 90,
                  max: 130,
                  divisions: 8,
                  onChanged: (v) =>
                      c.updateSettings(textSizePercent: v.round()),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildTextField({
    required TextEditingController controller,
    required SageTheme theme,
    required String hint,
    bool obscure = false,
  }) {
    return TextField(
      controller: controller,
      obscureText: obscure,
      style: theme.code,
      cursorColor: SageColors.accent,
      decoration: InputDecoration(
        hintText: hint,
        hintStyle: theme.code.copyWith(color: SageColors.textMuted),
        filled: true,
        fillColor: SageColors.surface,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 12,
          vertical: 12,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(SageTheme.chipRadius),
          borderSide: const BorderSide(color: SageColors.border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(SageTheme.chipRadius),
          borderSide: const BorderSide(color: SageColors.accent),
        ),
      ),
    );
  }
}

class _ToggleRow extends StatelessWidget {
  final String label;
  final bool value;
  final SageTheme theme;
  final ValueChanged<bool> onChanged;

  const _ToggleRow({
    required this.label,
    required this.value,
    required this.theme,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(label, style: theme.body),
        Switch(
          value: value,
          activeThumbColor: SageColors.accent,
          onChanged: onChanged,
        ),
      ],
    );
  }
}

class _OutlinedButton extends StatelessWidget {
  final String label;
  final SageTheme theme;
  final VoidCallback? onTap;

  const _OutlinedButton({required this.label, required this.theme, this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: MouseRegion(
        cursor: onTap != null
            ? SystemMouseCursors.click
            : SystemMouseCursors.basic,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: SageColors.surface,
            borderRadius: BorderRadius.circular(SageTheme.chipRadius),
            border: Border.all(color: SageColors.border),
          ),
          child: Text(label, style: theme.body),
        ),
      ),
    );
  }
}

class _AccentButton extends StatelessWidget {
  final String label;
  final SageTheme theme;
  final VoidCallback onTap;

  const _AccentButton({
    required this.label,
    required this.theme,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          decoration: BoxDecoration(
            color: SageColors.accent,
            borderRadius: BorderRadius.circular(SageTheme.chipRadius),
          ),
          child: Text(
            label,
            style: theme.body.copyWith(
              color: SageColors.bg,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
      ),
    );
  }
}
