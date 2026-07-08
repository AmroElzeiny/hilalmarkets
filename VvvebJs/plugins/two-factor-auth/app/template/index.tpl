import(common.tpl)


[data-auth-config-url]|href = <?php echo Vvveb\url(['module' => 'plugins/two-factor-auth/user', 'user_id' => $this->user['user_id']]);?>

[data-qrimage]|src = $this->qrimage
[data-code]        = $this->code
[data-secret]      = $this->secret
