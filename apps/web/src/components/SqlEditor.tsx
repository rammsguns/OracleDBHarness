import Editor from "@monaco-editor/react";

interface Props {
  value: string;
  onChange: (value: string) => void;
  language?: "sql" | "plaintext";
  height?: number;
}

/**
 * Monaco, with a plain textarea fallback while it loads so the console is usable
 * even if the editor bundle fails to arrive.
 */
export function SqlEditor({ value, onChange, language = "sql", height = 220 }: Props) {
  return (
    <div className="editor" style={{ height }}>
      <Editor
        height={height}
        language={language}
        value={value}
        onChange={(next) => onChange(next ?? "")}
        loading={
          <textarea
            className="editor-fallback"
            value={value}
            onChange={(event) => onChange(event.target.value)}
          />
        }
        options={{
          minimap: { enabled: false },
          fontSize: 13,
          scrollBeyondLastLine: false,
          automaticLayout: true,
          tabSize: 2,
        }}
      />
    </div>
  );
}
