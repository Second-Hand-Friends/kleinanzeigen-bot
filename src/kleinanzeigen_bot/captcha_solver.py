import os, logging, tempfile, random
import urllib.request

from typing import Final

import speech_recognition
from pydub import AudioSegment

from .web_scraping_mixin import By, WebScrapingMixin
from .i18n import get_translating_logger

LOG:Final[logging.Logger] = get_translating_logger(__name__)


class CaptchaSolver(WebScrapingMixin):

    def __init__(self) -> None:
        super().__init__()

        # Initialise speech recognition API object
        self._recognizer = speech_recognition.Recognizer()

    async def solve_captcha(self) -> bool:
        """
        Attempt to solve the reCAPTCHA challenge.
        """
        try:
            await self.web_click(By.ID, "recaptcha-anchor")

            if await self.is_solved():
                return True

            await self.web_sleep()

            await self.web_find(By.XPATH, '//iframe[contains(@src, "recaptcha") and contains(@src, "bframe")]', timeout=2)
            await self.web_click(By.XPATH, '//*[@id="recaptcha-audio-button"]', timeout=2)

            audio_src_elem = await self.web_find(By.XPATH, '//audio[@id=audio-source]', timeout=2)
            src = audio_src_elem.attrs["src"]

            response_text = await self._process_audio_challenge(src)
            if not response_text:
                return False

            await self.web_input(By.ID, 'audio-response', response_text)
            await self.web_click(By.ID, 'recaptcha-verify-button', timeout=5)
            await self.web_sleep()

            return await self.is_solved()

        except TimeoutError as ex:
            LOG.debug(ex, exc_info = True)
            return False

    async def _process_audio_challenge(self, audio_url: str) -> str | None:
        """Process audio challenge and return the recognized text.

       @param audio_url: URL of the audio file to process
       @return: recognized text from the audio file
       """

        # get temporary directory and create temporary files
        tmp_dir = tempfile.gettempdir()
        tmp_name = random.randrange(1,1000)

        mp3_file, wav_file = os.path.join(tmp_dir, f'{tmp_name}.mp3'), os.path.join(tmp_dir, f'{tmp_name}.wav')

        try:
            urllib.request.urlretrieve(audio_url, mp3_file)

            AudioSegment.from_mp3(mp3_file).export(wav_file, format="wav")

            with speech_recognition.AudioFile(wav_file) as source:
                # Disable dynamic energy threshold to avoid failed reCAPTCHA audio transcription due to static noise
                self._recognizer.dynamic_energy_threshold = False
                audio = self._recognizer.record(source)

            return self._recognizer.recognize_google(audio)

        except Exception as ex:
            LOG.debug(ex, exc_info=True)
            return None
        finally:
            for path in (mp3_file, wav_file):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    async def is_solved(self) -> bool:
        """
        Check if the captcha has been solved successfully.
        """
        try:
            await self.web_find(By.XPATH, "//div[@id=rc-anchor-container]//*//div[@class=recaptcha-checkbox-checkmark]", timeout=2)
        except TimeoutError:
            return False

        return True
