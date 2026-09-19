import os

def generate_page(md_path, out_path, page_name, title, icon_name, desc):
    with open(md_path, 'r', encoding='utf-8') as f:
        md_content = f.read()
    
    # We rebranded to Erex earlier, but let's make sure the markdown text also says Erex.
    md_content = md_content.replace('BlockHost', 'Erex').replace('Blockhost', 'Erex').replace('blockhost.gg', 'erex.gg')

    tsx = f"""import React from 'react';
import ReactMarkdown from 'react-markdown';
import {{ Shield, FileText }} from 'lucide-react';
import {{ PageHeader }} from '../components/PageHeader';

const content = `{md_content.replace('`', '\\`')}`;

export const {page_name}: React.FC = () => {{
  return (
    <div className="min-h-screen bg-[#090d16] text-slate-100 pb-20">
      <PageHeader
        badge="Legal & Compliance"
        badgeIcon={{{icon_name} === 'Shield' ? <Shield className="w-3.5 h-3.5" /> : <FileText className="w-3.5 h-3.5" />}}
        title="{title}"
        highlightedTitle=""
        description="{desc}"
        crumbs={{{{ label: '{title}' }}}}
      />
      
      <main className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 mt-12">
        <div className="prose prose-invert prose-emerald max-w-none bg-[#070a12] p-8 sm:p-12 rounded-3xl border border-slate-800 shadow-2xl">
          <ReactMarkdown>{{content}}</ReactMarkdown>
        </div>
      </main>
    </div>
  );
}};
"""
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(tsx)
    print(f"Generated {out_path}")

def main():
    generate_page(
        '/home/seems/blockhost/PRIVACY.md',
        '/home/seems/blockhost/blockhost-website/src/pages/PrivacyPage.tsx',
        'PrivacyPage',
        'Privacy Policy',
        'Shield',
        'How Erex collects, uses, and protects your personal data.'
    )
    generate_page(
        '/home/seems/blockhost/TERMS.md',
        '/home/seems/blockhost/blockhost-website/src/pages/TermsPage.tsx',
        'TermsPage',
        'Terms and Conditions',
        'FileText',
        'The terms of service that govern your use of the Erex platform.'
    )

if __name__ == '__main__':
    main()
