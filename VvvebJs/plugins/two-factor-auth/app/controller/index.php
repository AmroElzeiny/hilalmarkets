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
use function Vvveb\siteSettings;
use Vvveb\System\User\User;

class Index extends Base {
	function index() {
		$user_id = $this->global['user_id'];

		$user   = User::get(['user_id' => $user_id]);
		$secret  = $user['secret'];

		$this->view->enabled = $secret ? true : false;

		if (! $this->view->enabled) {
			$site  = siteSettings($this->global['site_id'], $this->global['language_id']);
			$title = $site['description']['title'] ?? 'Vvveb';

			$tfa = $this->init2fa($title);
			if (! $secret && ! ($secret = session('2fasecret'))) {
				$secret = $tfa->createSecret();
				session(['2fasecret' => $secret]);

				try {
					if (function_exists('socket_create')) {
						$tfa->ensureCorrectTime();
						$this->view->success[] = 'Your hosts time seems to be correct / within margin';
					}
				} catch (\RobThree\Auth\TwoFactorAuthException $ex) {
					$this->view->warning[] = 'Your server time seems to be off: ' . $ex->getMessage();
				}
			}

			$this->view->qrimage = $tfa->getQRCodeImageAsDataUri($user['email'], $secret);
			$this->view->secret  = chunk_split($secret, 4, ' ');
		}
	}

	function disable() {
		$user_id = $this->global['user_id'];
		$user   = User::update(['secret' => ''],['user_id' => $user_id]);

		$this->index();
	}

	function save() {
		$user_id  = $this->global['user_id'];

		$settings = $this->request->post['settings'] ?? [];
		$secret   = str_replace(' ', '', $settings['secret'] ?? '');
		$code     = str_replace(' ', '', $settings['code'] ?? '');

		$tfa = $this->init2fa();
		if ($tfa->verifyCode($secret, $code) === true) {
			$user = User::update(['secret' => $secret],['user_id' => $user_id]);
		} else {
			$this->view->errors[] = __('Invalid code!');
		}

		$this->index();
	}
}
