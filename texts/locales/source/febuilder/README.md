# FEBuilder FE8 text-ID map snapshot

`translate_textid_FE8.txt` is the byte-exact input used by the independent
FEBuilder alignment-evidence importer. It was copied from FEBuilderGBA commit
`2e4396efd14638ee03ada051eedfa40b66ff0ea3`:

```text
config/data/translate_textid_FE8.txt
SHA-256 d9f0fc8ede5820bb4b93299ad08286055a56037bb1fbdb6bf589ad1f7af16734
```

The parser behavior was independently implemented from these pinned upstream
files:

```text
FEBuilderGBA/TranslateTextUtil.cs
SHA-256 0b965691a819133705c0b94e4474da68c25b2a133b01615c69cc99f3385faec4

FEBuilderGBA/U.cs
SHA-256 fc1dc7ccd0ff3af089f428f94eaa8fa8419cd0368ec11756b965f94e429d895f
```

Normal checks read only this committed snapshot. They do not require a sibling
FEBuilderGBA checkout.
