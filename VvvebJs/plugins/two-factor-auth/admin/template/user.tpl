import(common.tpl)


[data-auth-config-url]|href = <?php echo Vvveb\url(['module' => 'plugins/two-factor-auth/user', 'type' => $this->type, $this->type . '_id' => $this->user[$this->type . '_id']]);?>

[data-qrimage]|src = $this->qrimage
[data-code]        = $this->code
[data-secret]      = $this->secret
