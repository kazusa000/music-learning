# AD2 resource-path repair and deferred admission

Date: 2026-10-21. User requested REA diagnosis, prohibited Computer Use, then chose to defer AD2 and consider Kontakt Factory Library 2 drums.

## Findings and applied repair

- Installed plugin: `C:\Program Files\Common Files\VST3\XLN Audio\Addictive Drums 2.vst3`, reported version 2.9.0, SHA-256 `d4e9369b5c7cf5f8d2df1b027bf76a989443382c70cd9c38cbc2892bf6043ae6`.
- Original native-host SHA-256: `258cc9f1224b95405db6c64a59abcdae1c99ab8b89bb21738ceabc6a9b596e86`. No host rebuild, plugin modification, licensing changes, or formal project/API operations were performed.
- Controlled Windows debug capture reproduced `0xC0000005`, a null-pointer read at plugin RVA `0x60C98F`. A separate SDK probe reproduced it inside `IPluginFactory::createInstance(IComponent)`, before the host context was passed to `initialize`.
- REA/Ghidra identified the containing constructor as `cCottonGlobals`, with a resource-dependent virtual call carrying `luaInit`. Evidence: `ev_17a3444b66539e074d1d85b0cf7ff2b1282a3552ef719526a38c6f8c27ed5925`. Binary identity/overview: `ev_56091b67c43eaf93b1c85c5b076e69a61af5ec6545fd0b95ce7d77aec60c004b`.
- The standard locator `C:\ProgramData\XLN Audio\Addictive Drums 2\App\config.txt` did not exist. It was created with `app` and `sound` pointing to the existing `App` and `Sound Data` beneath `H:\music\tmp\XLN Audio - Addictive Drums 2 Complete v2.9.0.4\Addictive Drums 2(1)`. Samples were not moved or copied. The locator is a small configuration file, not a C-drive sample installation.
- With that locator, the unchanged native host scanned the unchanged plugin successfully. The independent SDK lifecycle also passed component creation, initialization, bus queries, termination, and release. It reported 14 audio output buses and one event input bus.

## Reusable maintenance command

`scripts/configure_ad2_library.py` validates the selected resource root, the Lua/system/sound Xpak headers, and the presence of ADpak data before configuring anything. Default invocation is read-only. `--apply` exclusively creates a missing locator, leaves an already-correct locator untouched, and refuses to overwrite a different existing locator. It runs no installer, plugin, activation utility, model, or project service.

```powershell
& data/envs/app/Scripts/python.exe scripts/configure_ad2_library.py --library-root 'H:\music\tmp\XLN Audio - Addictive Drums 2 Complete v2.9.0.4\Addictive Drums 2(1)'
```

Five regression tests passed: read-only checks and idempotence; missing Lua resources; wrong root/corrupt header; preservation of existing configuration; missing ADpak samples. Ruff passed for the new script and tests.

## Remaining limits; AD2 deferred

- Successful scanning is not successful drum admission. Independent 5-second first/reopened audio probes produced zero PCM; captured state had empty preset identity and all kit-piece selections at zero. Trying the state's `DoLoadDefaultPreset` flag did not select a kit. These failures were retained in reports.
- A normal controller API probe found `IUnitInfo` but zero exposed program lists. Visible VST3 controls did not expose drum-kit selection. No kit IDs were guessed, no validation was relaxed, and no AD2 entry was admitted.
- The user supplied a standalone screenshot reporting that its binary is in H staging while the installation record expects `C:\Program Files\XLN Audio\Addictive Drums 2`. This standalone-path mismatch was not repaired before the user chose to defer AD2. It must not be described as fixed.
- Further AD2 repair, sound validation, migration, and admission are deferred. No GUI was operated. The REA session was exported and closed.
- Local evidence is under ignored `runtime/ad2-rea-debug-20261021/`: original and post-fix captures/manifests, controlled audio results, and `rea-evidence.json`. Future library migration must deliberately update the locator; it currently references staging.

## Alternative already present

Read-only inventory of `Kontakt Factory Library 2 v1.4.0 [Native Instruments]\Instruments` found 13 `Band\7 - Drum Kits` NKI presets, including Rock Kit, Pop Kit, Jazz Kit, Funk Kit, and Studio Break Kit; 90 `Beats` NKI presets; and 11 `Orchestral\3 - Percussion` NKI presets. Counts are preset files, not unique sampled instrument models. This inventory is not yet a Kontakt load/audio test.
