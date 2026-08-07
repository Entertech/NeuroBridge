#ifndef C73906B4_CE21_491E_9DB5_BF937F40CFB5
#define C73906B4_CE21_491E_9DB5_BF937F40CFB5
#include "Data.h"
#include "Device.h"
#include "NumCpp.hpp"
#include "TypeDefine.h"
namespace basic
{
    namespace dsp
    {
        namespace eeghandler
        {
            struct EEGHandlerTemp
            {
                nc::NdArray<double> eegData;
            };

            struct EEGHandlerResult
            {
                EEGQuality quality;
                nc::NdArray<double> eegWave;
                EEGPower power;
                nc::NdArray<double> featureWave;
                EEGPower featurePower;
            };

            EEGHandlerResult handler(const vectord &eegRawData, double splitLen, double epochLen,
                                     DeviceInfo *deviceInfo, EEGHandlerTemp &tmp);
        }

    } // namespace dsp

} // namespace basic

#endif /* C73906B4_CE21_491E_9DB5_BF937F40CFB5 */
