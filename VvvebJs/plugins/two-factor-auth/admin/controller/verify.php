<?php

/**
 * Vvveb
 *
 * Copyright (C) 2022  Ziadin Givan
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as
 * published by the Free Software Foundation, either version 3 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 *
 */

namespace Vvveb\Plugins\TwoFactorAuth\Controller;

use function Vvveb\__;
use function Vvveb\session;
use Vvveb\System\User\Admin;

class Verify extends Base {
	function index() {
		$code = $this->request->post['code'] ?? null;

		if ($code && $admin = Admin::current()) {
			$secret = $admin['secret'];
			$tfa = $this->init2fa();
			$code = str_replace(' ', '', $code);

			if ($tfa->verifyCode($secret, $code) === true) {
				session(['2facode' => $code]);
				$this->redirect(['module' => 'index']);
			} else {
				$this->view->errors[] = __('Invalid code!');
			}
		}
	}
}
