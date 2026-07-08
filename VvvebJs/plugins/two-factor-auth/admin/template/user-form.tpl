.details|append = from(/public/plugins/two-factor-auth/admin/user-form.html|body > *)

[data-auth-config-url]|if_exists = $this->user[$this->type . '_id'] 
[data-auth-config-url]|href = <?php echo Vvveb\url(['module' => 'plugins/two-factor-auth/user', 'type' => $this->type, $this->type . '_id' => $this->user[$this->type . '_id'] ?? '']);?>
