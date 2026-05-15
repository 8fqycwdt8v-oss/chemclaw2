'use client';
import { useCallback, useEffect, useRef } from 'react';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import type { JSONContent } from '@tiptap/react';

interface WikiEditorProps {
  initialContent?: JSONContent;
  onSave: (content: JSONContent, text: string) => void;
  readOnly?: boolean;
}

export function WikiEditor({ initialContent, onSave, readOnly = false }: WikiEditorProps) {
  // Track whether the editor has unsaved changes
  const isDirtyRef = useRef(false);

  const editor = useEditor({
    extensions: [
      StarterKit,
      Placeholder.configure({ placeholder: 'Start writing…' }),
    ],
    content: initialContent,
    editable: !readOnly,
    immediatelyRender: false,
    onUpdate: () => {
      isDirtyRef.current = true;
    },
  });

  // Warn on browser close/tab switch when there are unsaved changes
  useEffect(() => {
    if (readOnly) return;
    const handler = (e: BeforeUnloadEvent) => {
      if (!isDirtyRef.current) return;
      e.preventDefault();
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [readOnly]);

  const handleSave = useCallback(() => {
    if (!editor) return;
    onSave(editor.getJSON(), editor.getText());
    isDirtyRef.current = false;
  }, [editor, onSave]);

  if (!editor) return null;

  return (
    <div className="wiki-editor">
      {!readOnly && (
        <div className="toolbar flex gap-2 mb-2">
          <button
            onClick={() => editor.chain().focus().toggleBold().run()}
            className={editor.isActive('bold') ? 'font-bold' : ''}
          >
            B
          </button>
          <button
            onClick={() => editor.chain().focus().toggleItalic().run()}
            className={editor.isActive('italic') ? 'italic' : ''}
          >
            I
          </button>
          <button onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}>
            H2
          </button>
          <button
            className="ml-auto bg-blue-600 text-white px-3 py-1 rounded text-sm"
            onClick={handleSave}
          >
            Save
          </button>
        </div>
      )}
      <EditorContent editor={editor} className="prose max-w-none" />
    </div>
  );
}
