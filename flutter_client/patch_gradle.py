import sys

with open('android/app/build.gradle.kts', 'r') as f:
    lines = f.readlines()

new_lines = []
in_dependencies = False
has_dependencies = False

for line in lines:
    if "compileOptions {" in line:
        new_lines.append(line)
        new_lines.append("        isCoreLibraryDesugaringEnabled = true\n")
        continue
    if "dependencies {" in line:
        has_dependencies = True
        new_lines.append(line)
        new_lines.append("    coreLibraryDesugaring(\"com.android.tools:desugar_jdk_libs:2.0.4\")\n")
        continue
    new_lines.append(line)

if not has_dependencies:
    new_lines.append("\ndependencies {\n")
    new_lines.append("    coreLibraryDesugaring(\"com.android.tools:desugar_jdk_libs:2.0.4\")\n")
    new_lines.append("}\n")

with open('android/app/build.gradle.kts', 'w') as f:
    f.writelines(new_lines)
