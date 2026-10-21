# 播放音频与解码缓存的生命周期

2026-10-21。用户在397秒歌曲重建界面遇到 `Cannot read properties of undefined (reading 'duration')`。

Transport原先等待所有音频解码完成后，启动voice时再次按ID读取可回收的buffers Map。解码缓存有1 GiB限额；后台warm也会按另一组源裁剪缓存。解码完成与实际启动之间仍有异步预取窗口，缓存不是播放事务持有音频的可靠依据。独立回归在原提交f2a26a7上交错运行真实warm裁剪，复现完全相同的duration异常。

现在play直接保存每个源已解码的AudioBuffer及duration，首次播放及preserve中的新增／换源voice都接收这份引用，由WebAudio继续持有。缓存仍可回收，没有提高缓存上限或忽略错误，流式音轨仍走原首块预取及常驻PCM路径，代次取消检查保留。

## 验证

- `node --experimental-strip-types transport-cache-unit.mjs --baseline=f2a26a7`：旧实现复现相同duration异常。
- 默认同一回归通过：397秒／8立体声轨元数据触发真实1 GiB缓存回收；真实warm交错删除缓存、暂停重播、preserve换源、播放中监听复用、延迟解码取消均通过。长曲缓冲为元数据替身，不冒充整曲真实音频听辨。
- `node transport-cache-browser.mjs`：独立本机Vite＋真实Edge/WebAudio，8条实际解码非零PCM，解码后交错真实warm裁剪缓存；即时音量／静音／独奏（仅1轨可听且voice不重启）、暂停重播、原曲／重建时钟连续切换、循环均通过，0 JS错误。该探针不访问正式API，不创建或修改音乐工程，不调用模型／插件／NAS或音源库。
- 7组相关前端单元及TS/Vite构建通过。正式8765只读HTTP确认已经提供新构建 `index-B3LrRqPN.js`；已有窗口需刷新或重开。没有后端、数据库、模型、原生宿主或Pianoteq验证文件变更。

探针直接生成短WAV并经HTTP送入浏览器，不在磁盘生成长曲测试音频。没有执行清理命令或触碰历史拒绝清理的目录。
