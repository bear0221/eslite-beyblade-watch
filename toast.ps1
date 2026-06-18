# toast.ps1 — 顯示 Windows 桌面通知。
# 通知標題與內容透過環境變數傳入,避免中文在命令列被亂碼。
$ErrorActionPreference = 'Stop'

$title = $env:ESLITE_TOAST_TITLE
$msg   = $env:ESLITE_TOAST_MSG
if (-not $title) { $title = '誠品戰鬥陀螺監控' }
if (-not $msg)   { $msg   = '(無內容)' }

try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $texts = $xml.GetElementsByTagName('text')
    $texts.Item(0).AppendChild($xml.CreateTextNode($title)) | Out-Null
    $texts.Item(1).AppendChild($xml.CreateTextNode($msg))   | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    # 借用系統內建的 PowerShell AppId,免安裝任何模組即可顯示通知
    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
} catch {
    # 通知失敗不影響主程式(主程式仍會寫 log / 響鈴),回傳非 0 讓呼叫端知道
    exit 1
}
