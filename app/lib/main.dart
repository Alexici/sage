import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'state/sage_controller.dart';
import 'theme/sage_theme.dart';
import 'ui/chat_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(
    ChangeNotifierProvider(
      create: (_) => SageController(),
      child: const SageApp(),
    ),
  );
}

class SageApp extends StatelessWidget {
  const SageApp({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = context.watch<SageController>();
    final sageTheme = SageTheme(textScale: controller.textScale);

    return MaterialApp(
      title: 'Sage',
      debugShowCheckedModeBanner: false,
      theme: sageTheme.toMaterialTheme(),
      home: ChatScreen(controller: controller),
    );
  }
}
