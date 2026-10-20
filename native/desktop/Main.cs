using System;
using System.Drawing;
using System.Windows.Forms;
using System.Runtime.InteropServices;
using System.IO;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8", FrameworkDisplayName=".NET Framework 4.8")]

sealed class StudioWindow : Form {
    readonly WebView2 web = new WebView2();
    readonly Uri target;
    readonly string profile;
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly string diagnostic;
    [DllImport("user32.dll")] static extern IntPtr GetThreadDpiAwarenessContext();
    [DllImport("user32.dll")] static extern IntPtr GetWindowDpiAwarenessContext(IntPtr window);
    [DllImport("user32.dll")] static extern bool AreDpiAwarenessContextsEqual(IntPtr first, IntPtr second);
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr window);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    readonly IntPtr originalForeground = GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool ReleaseCapture();
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
    protected override bool ShowWithoutActivation { get { return diagnostic != null; } }
    public StudioWindow(string url, string data, string report = null) {
        diagnostic = report;
        if (diagnostic != null) { ShowInTaskbar = false; Opacity = 0; }
        target = new Uri(url); profile = data;
        if (target.Scheme != "http" || target.Host != "127.0.0.1") throw new ArgumentException("Local application URL required");
        Text = "Music Learning"; BackColor = Color.FromArgb(16,16,17);
        AutoScaleDimensions = new SizeF(96,96); AutoScaleMode = AutoScaleMode.Dpi;
        FormBorderStyle = FormBorderStyle.None; Padding = new Padding(5);
        MinimumSize = new Size(960,640); Size = new Size(1520,980); StartPosition = FormStartPosition.CenterScreen;
        web.Dock = DockStyle.Fill; web.DefaultBackgroundColor = BackColor; Controls.Add(web);
        Shown += async (s,e) => {
            try {
                var env = await CoreWebView2Environment.CreateAsync(null, profile);
                await web.EnsureCoreWebView2Async(env);
                var core = web.CoreWebView2;
                core.Settings.IsStatusBarEnabled = false;
                core.Settings.IsZoomControlEnabled = false;
                core.Settings.AreBrowserAcceleratorKeysEnabled = false;
                core.Settings.IsGeneralAutofillEnabled = false;
                core.Settings.IsPasswordAutosaveEnabled = false;
                core.NavigationStarting += (sender,args) => {
                    Uri next; if (!Uri.TryCreate(args.Uri,UriKind.Absolute,out next) || next.GetLeftPart(UriPartial.Authority) != target.GetLeftPart(UriPartial.Authority)) args.Cancel = true;
                };
                core.NewWindowRequested += (sender,args) => { args.Handled = true; };
                core.WebMessageReceived += (sender,args) => {
                    Uri source; if (!Uri.TryCreate(args.Source,UriKind.Absolute,out source) || source.GetLeftPart(UriPartial.Authority) != target.GetLeftPart(UriPartial.Authority)) return;
                    string action; try { action = args.TryGetWebMessageAsString(); } catch { return; }
                    if (action == "minimize") WindowState = FormWindowState.Minimized;
                    else if (action == "maximize") WindowState = WindowState == FormWindowState.Maximized ? FormWindowState.Normal : FormWindowState.Maximized;
                    else if (action == "close") Close();
                    else if (action == "drag") { ReleaseCapture(); SendMessage(Handle,0xA1,new IntPtr(2),IntPtr.Zero); }
                };
                await core.AddScriptToExecuteOnDocumentCreatedAsync("Object.defineProperty(window,'mlDesktop',{value:{post:function(a){chrome.webview.postMessage(a);}},writable:false});document.addEventListener('DOMContentLoaded',function(){document.documentElement.dataset.desktop='native';});");
                core.NavigationCompleted += async (sender,args) => {
                    PublishState();
                    if (diagnostic != null) {
                        try {
                            await Task.Delay(500);
                            var metrics = await core.ExecuteScriptAsync("JSON.stringify({dpr:devicePixelRatio,width:innerWidth,height:innerHeight,screenWidth:screen.width,screenHeight:screen.height,visualScale:visualViewport.scale})");
                            File.WriteAllText(diagnostic,json.Serialize(new {
                                per_monitor_v2 = AreDpiAwarenessContextsEqual(GetThreadDpiAwarenessContext(), new IntPtr(-4)),
                                window_per_monitor_v2 = AreDpiAwarenessContextsEqual(GetWindowDpiAwarenessContext(Handle), new IntPtr(-4)),
                                window_dpi = GetDpiForWindow(Handle), device_dpi = DeviceDpi,
                                web_pixels = new { width = web.ClientSize.Width, height = web.ClientSize.Height },
                                viewport = json.DeserializeObject(json.Deserialize<string>(metrics)), zoom = web.ZoomFactor,
                                os = Environment.OSVersion.VersionString,
                                navigation_success = args.IsSuccess,
                                foreground_unchanged = GetForegroundWindow() == originalForeground
                            }));
                            using (var image = File.Create(Path.ChangeExtension(diagnostic,"png"))) await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                        } catch (Exception ex) { File.WriteAllText(diagnostic,json.Serialize(new { error = ex.ToString() })); }
                        finally { Close(); }
                    }
                };
                web.Source = target;
            } catch(Exception ex) {
                Controls.Clear(); var label = new Label { Dock=DockStyle.Fill, ForeColor=Color.White, Text="桌面窗口启动失败，请检查 WebView2 Runtime。\n"+ex.Message, Padding=new Padding(30) }; Controls.Add(label);
                Console.Error.WriteLine(ex.ToString());
                if (diagnostic != null) { File.WriteAllText(diagnostic,json.Serialize(new { error = ex.ToString() })); Close(); }
            }
        };
        Resize += (s,e) => PublishState();
    }
    void UpdateMonitorMetrics() {
        var area = Screen.FromHandle(Handle).WorkingArea;
        MaximizedBounds = area;
        MinimumSize = new Size(Math.Min(LogicalToDeviceUnits(960),area.Width), Math.Min(LogicalToDeviceUnits(640),area.Height));
        Padding = new Padding(LogicalToDeviceUnits(5));
    }
    protected override void OnLoad(EventArgs e) {
        base.OnLoad(e);
        UpdateMonitorMetrics();
        var area = Screen.FromHandle(Handle).WorkingArea;
        Size = new Size(Math.Min(LogicalToDeviceUnits(1520),(int)(area.Width*.92)),Math.Min(LogicalToDeviceUnits(980),(int)(area.Height*.92)));
        Location = new Point(area.X+(area.Width-Width)/2,area.Y+(area.Height-Height)/2);
    }
    protected override async void OnDpiChanged(DpiChangedEventArgs e) {
        base.OnDpiChanged(e);
        UpdateMonitorMetrics();
        if (web.CoreWebView2 != null) {
            try { await web.CoreWebView2.ExecuteScriptAsync("requestAnimationFrame(function(){window.dispatchEvent(new Event('resize'));})"); }
            catch (Exception ex) { Console.Error.WriteLine(ex.ToString()); }
        }
    }
    protected override void OnLocationChanged(EventArgs e) {
        base.OnLocationChanged(e);
        if (IsHandleCreated) MaximizedBounds = Screen.FromHandle(Handle).WorkingArea;
    }
    void PublishState() { if (web.CoreWebView2 != null) web.CoreWebView2.PostWebMessageAsJson(json.Serialize(new { type="window-state", maximized=WindowState==FormWindowState.Maximized })); }
    protected override void WndProc(ref Message m) {
        if(m.Msg == 0xA3 && m.WParam.ToInt32() == 2) { WindowState=WindowState==FormWindowState.Maximized?FormWindowState.Normal:FormWindowState.Maximized; m.Result=IntPtr.Zero; return; }
        if (m.Msg == 0x84 && WindowState == FormWindowState.Normal) {
            var p = PointToClient(new Point((short)(m.LParam.ToInt64() & 0xffff),(short)((m.LParam.ToInt64() >> 16)&0xffff)));
            int edge = LogicalToDeviceUnits(5);
            bool l=p.X<edge,r=p.X>=ClientSize.Width-edge,t=p.Y<edge,b=p.Y>=ClientSize.Height-edge;
            int hit=t?(l?13:r?14:12):b?(l?16:r?17:15):l?10:r?11:0;
            if (hit!=0) { m.Result=new IntPtr(hit); return; }
        }
        base.WndProc(ref m);
    }
    protected override void Dispose(bool disposing) { if(disposing) web.Dispose(); base.Dispose(disposing); }
    [STAThread] static void Main(string[] args) {
        bool probe = args.Length == 4 && args[0] == "--diagnose";
        if(args.Length!=2 && !probe) return;
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(probe ? new StudioWindow(args[1],args[2],args[3]) : new StudioWindow(args[0],args[1]));
    }
}
