import os

def replace_in_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    new_content = content.replace('BlockHost', 'Erex')
    new_content = new_content.replace('Blockhost', 'Erex')
    new_content = new_content.replace('blockhost.gg', 'erex.gg')
    new_content = new_content.replace('blockhost_user', 'erex_user')
    new_content = new_content.replace('blockhost', 'erex')

    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Updated {filepath}")

def main():
    target_dir = '/home/seems/blockhost/blockhost-website/src'
    for root, _, files in os.walk(target_dir):
        for file in files:
            if file.endswith(('.tsx', '.ts', '.css', '.html')):
                replace_in_file(os.path.join(root, file))
    replace_in_file('/home/seems/blockhost/blockhost-website/index.html')

if __name__ == '__main__':
    main()
